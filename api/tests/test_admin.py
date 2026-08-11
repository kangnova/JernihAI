"""Tests admin (FR-13) — kontrol akses & statistik platform."""

import io

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.tasks.enhance as enhance_module
from app.core.config import settings
from app.db.session import get_db
from app.main import app
from app.models.base import Base


def _image_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 100, 50)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
async def db(tmp_path, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(settings, "celery_task_always_eager", True)
    monkeypatch.setattr(settings, "enhance_backend", "mock")
    monkeypatch.setattr(settings, "rate_limit_enabled", False)  # NFR-04
    monkeypatch.setattr(settings, "free_daily_quota", 3)  # deterministik (bukan .env dev)
    monkeypatch.setattr(settings, "admin_emails", [])  # default non-admin
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "result_dir", str(tmp_path / "results"))
    monkeypatch.setattr(enhance_module, "async_session_factory", factory)

    yield factory

    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.fixture()
async def client(db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _register(client, email: str = "user@example.com") -> None:
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "password123",
            "name": "Tono",
            "privacy_consent": True,
        },
    )
    assert resp.status_code == 201


async def _upload(client) -> None:
    resp = await client.post(
        "/api/v1/jobs",
        files={"file": ("foto.png", _image_bytes(), "image/png")},
        data={"scale": "2", "output_format": "webp"},
    )
    assert resp.status_code == 201


async def _login(client, email: str) -> None:
    """Login ulang (cookie berganti) — dipakai untuk memanggil endpoint admin."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "password123"},
    )
    assert resp.status_code == 200


async def test_admin_requires_auth(client):
    resp = await client.get("/api/v1/admin/stats")
    assert resp.status_code == 401


async def test_admin_denied_for_regular_user(client):
    await _register(client)
    resp = await client.get("/api/v1/admin/stats")
    assert resp.status_code == 403
    resp = await client.get("/api/v1/admin/jobs")
    assert resp.status_code == 403


async def test_admin_stats_counts(client, monkeypatch):
    """FR-13: admin melihat total user & job (lintas semua user)."""
    monkeypatch.setattr(settings, "admin_emails", ["boss@example.com"])
    await _register(client, "boss@example.com")
    await _register(client, "user-a@example.com")
    await _register(client, "user-b@example.com")
    await _upload(client)  # 1 job atas nama user-b
    await _login(client, "boss@example.com")  # kembali sebagai admin

    resp = await client.get("/api/v1/admin/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_users"] == 3
    assert body["total_jobs"] == 1
    assert body["jobs_by_status"]["completed"] == 1
    assert body["free_quota_limit"] == 3
    assert body["revenue_idr"] == 0


async def test_admin_jobs_lists_all_users(client, monkeypatch):
    """FR-13: daftar job mencakup job milik user lain + email pemilik."""
    monkeypatch.setattr(settings, "admin_emails", ["boss@example.com"])
    await _register(client, "boss@example.com")
    await _register(client, "other@example.com")
    await _upload(client)
    await _login(client, "boss@example.com")

    resp = await client.get("/api/v1/admin/jobs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["user_email"] == "other@example.com"
    assert body["items"][0]["original_name"] == "foto.png"


async def test_admin_me_exposes_is_admin(client, monkeypatch):
    """UserOut memuat is_admin — dipakai web untuk menampilkan link Admin."""
    monkeypatch.setattr(settings, "admin_emails", ["boss@example.com"])
    await _register(client, "boss@example.com")
    me = (await client.get("/api/v1/auth/me")).json()
    assert me["is_admin"] is True

    await _register(client, "biasa@example.com")
    me = (await client.get("/api/v1/auth/me")).json()
    assert me["is_admin"] is False


# --- Alat admin: reset kuota & hapus job uji ---


async def _quota_used(client) -> int:
    return (await client.get("/api/v1/quota")).json()["used"]


async def test_admin_quota_reset_single_user(client, monkeypatch):
    """Reset kuota satu user via email — admin only."""
    monkeypatch.setattr(settings, "admin_emails", ["boss@example.com"])
    await _register(client, "boss@example.com")
    await _register(client, "sasaran@example.com")
    await _upload(client)  # sasaran pakai 1 kuota
    assert await _quota_used(client) == 1
    await _login(client, "boss@example.com")

    resp = await client.post(
        "/api/v1/admin/quota/reset",
        json={"email": "sasaran@example.com"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"reset": 1, "email": "sasaran@example.com"}

    await _login(client, "sasaran@example.com")
    assert await _quota_used(client) == 0


async def test_admin_quota_reset_all_users(client, monkeypatch):
    """all=true mereset SEMUA user."""
    monkeypatch.setattr(settings, "admin_emails", ["boss@example.com"])
    await _register(client, "boss@example.com")
    await _register(client, "a@example.com")
    await _register(client, "b@example.com")
    for _ in range(2):
        await _upload(client)
    await _login(client, "boss@example.com")

    resp = await client.post("/api/v1/admin/quota/reset", json={"all": True})
    assert resp.status_code == 200
    assert resp.json()["reset"] == 3


async def test_admin_quota_reset_requires_email_or_all(client, monkeypatch):
    """Tanpa email & tanpa all -> 400."""
    monkeypatch.setattr(settings, "admin_emails", ["boss@example.com"])
    await _register(client, "boss@example.com")
    await _login(client, "boss@example.com")

    resp = await client.post("/api/v1/admin/quota/reset", json={})
    assert resp.status_code == 400


async def test_admin_quota_reset_unknown_email_404(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_emails", ["boss@example.com"])
    await _register(client, "boss@example.com")
    await _login(client, "boss@example.com")

    resp = await client.post(
        "/api/v1/admin/quota/reset", json={"email": "ghost@example.com"}
    )
    assert resp.status_code == 404


async def test_admin_delete_job_removes_row_and_files(db, client, monkeypatch, tmp_path):
    """Hapus job admin: baris DB + file original & hasil hilang dari disk."""
    from pathlib import Path

    from app.models.job import Job

    monkeypatch.setattr(settings, "admin_emails", ["boss@example.com"])
    await _register(client, "boss@example.com")
    await _register(client, "korban@example.com")
    resp = await client.post(
        "/api/v1/jobs",
        files={"file": ("foto.png", _image_bytes(), "image/png")},
        data={"scale": "2", "output_format": "webp"},
    )
    job_id = resp.json()["id"]
    orig = Path(settings.upload_dir) / f"{job_id}.png"
    result = Path(settings.result_dir) / f"{job_id}.webp"
    assert orig.exists() and result.exists()

    await _login(client, "boss@example.com")
    resp = await client.delete(f"/api/v1/admin/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    assert resp.json()["files_deleted"] == 2

    # Baris & file hilang.
    async with db() as session:
        assert await session.get(Job, job_id) is None
    assert not orig.exists() and not result.exists()

    # Hapus lagi -> 404.
    resp = await client.delete(f"/api/v1/admin/jobs/{job_id}")
    assert resp.status_code == 404


async def test_admin_tools_denied_for_regular_user(client):
    """User biasa tidak boleh reset kuota / hapus job."""
    await _register(client)
    resp = await client.post("/api/v1/admin/quota/reset", json={"all": True})
    assert resp.status_code == 403
    resp = await client.delete("/api/v1/admin/jobs/whatever")
    assert resp.status_code == 403
    resp = await client.get("/api/v1/admin/users")
    assert resp.status_code == 403


# --- Direktori user: email, kuota, kredit, consent, jumlah riwayat ---


async def test_admin_users_lists_details(client, monkeypatch):
    """Admin melihat email/kuota/kredit/consent/job_count tiap user."""
    monkeypatch.setattr(settings, "admin_emails", ["boss@example.com"])
    await _register(client, "boss@example.com")
    await _register(client, "target@example.com")
    await _upload(client)  # 1 job atas nama target
    await _login(client, "boss@example.com")

    resp = await client.get("/api/v1/admin/users")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2

    target = next(u for u in body["items"] if u["email"] == "target@example.com")
    assert target["quota_used"] == 1
    assert target["quota_limit"] == 3
    assert target["quota_remaining"] == 2
    assert target["credit_balance"] == 0
    assert target["job_count"] == 1
    assert target["privacy_consent_at"] is not None
    assert target["provider"] == "local"


async def test_admin_users_search_by_email(client, monkeypatch):
    """Pencarian parsial email di direktori user."""
    monkeypatch.setattr(settings, "admin_emails", ["boss@example.com"])
    await _register(client, "boss@example.com")
    await _register(client, "alfa@example.com")
    await _register(client, "beta@example.com")
    await _login(client, "boss@example.com")

    resp = await client.get("/api/v1/admin/users?email=beta")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["email"] == "beta@example.com"


async def test_admin_jobs_filtered_by_email(client, monkeypatch):
    """Riwayat SATU user via filter email di GET /admin/jobs."""
    monkeypatch.setattr(settings, "admin_emails", ["boss@example.com"])
    await _register(client, "boss@example.com")
    await _register(client, "korban@example.com")
    await _upload(client)  # job korban
    await _upload(client)  # job korban kedua
    await _login(client, "boss@example.com")
    # Satu job tambahan atas nama boss.
    await _login(client, "boss@example.com")

    resp = await client.get("/api/v1/admin/jobs?email=korban@example.com")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert all(i["user_email"] == "korban@example.com" for i in body["items"])

    # Tanpa filter = semua.
    resp = await client.get("/api/v1/admin/jobs")
    assert resp.json()["total"] == 2


# --- Detail user: profil + transaksi kredit (halaman detail admin) ---


async def _user_id_by_email(client, email: str) -> str:
    resp = await client.get(f"/api/v1/admin/users?email={email}")
    return resp.json()["items"][0]["id"]


async def test_admin_user_detail_by_id(client, monkeypatch):
    """GET /admin/users/{id}: profil lengkap + kuota/kredit + job_count."""
    monkeypatch.setattr(settings, "admin_emails", ["boss@example.com"])
    await _register(client, "boss@example.com")
    await _register(client, "target@example.com")
    await _upload(client)  # 1 job atas nama target
    await _login(client, "boss@example.com")
    target_id = await _user_id_by_email(client, "target@example.com")

    resp = await client.get(f"/api/v1/admin/users/{target_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "target@example.com"
    assert body["name"] == "Tono"
    assert body["quota_used"] == 1
    assert body["quota_limit"] == 3
    assert body["quota_remaining"] == 2
    assert body["credit_balance"] == 0
    assert body["job_count"] == 1
    assert body["privacy_consent_at"] is not None

    # User tak dikenal -> 404.
    resp = await client.get("/api/v1/admin/users/tidak-ada")
    assert resp.status_code == 404


async def test_admin_user_detail_denied_for_regular_user(client):
    """User biasa tidak boleh melihat detail user."""
    await _register(client)
    resp = await client.get("/api/v1/admin/users/some-id")
    assert resp.status_code == 403
    resp = await client.get("/api/v1/admin/users/some-id/transactions")
    assert resp.status_code == 403
    resp = await client.delete("/api/v1/admin/users/some-id/jobs")
    assert resp.status_code == 403


async def test_admin_delete_all_user_jobs(db, client, monkeypatch, tmp_path):
    """Hapus SEMUA job satu user + file di disk — job user lain tidak tersentuh."""
    from pathlib import Path

    monkeypatch.setattr(settings, "admin_emails", ["boss@example.com"])
    await _register(client, "boss@example.com")
    await _register(client, "korban@example.com")
    await _upload(client)  # job korban 1
    await _upload(client)  # job korban 2
    await _register(client, "lain@example.com")
    await _upload(client)  # job milik user lain
    await _login(client, "boss@example.com")

    korban_id = await _user_id_by_email(client, "korban@example.com")
    korban_jobs = (await client.get("/api/v1/admin/jobs?email=korban@example.com")).json()
    korban_job_ids = [i["id"] for i in korban_jobs["items"]]
    lain_jobs = (await client.get("/api/v1/admin/jobs?email=lain@example.com")).json()
    lain_job_ids = [i["id"] for i in lain_jobs["items"]]
    # File korban ada di disk sebelum dihapus.
    korban_files = [
        Path(settings.upload_dir) / f"{jid}.png" for jid in korban_job_ids
    ] + [Path(settings.result_dir) / f"{jid}.webp" for jid in korban_job_ids]
    assert all(f.exists() for f in korban_files)

    resp = await client.delete(f"/api/v1/admin/users/{korban_id}/jobs")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"deleted": 2, "files_deleted": 4, "user_id": korban_id}

    # Job & file korban hilang; job user lain utuh.
    assert all(not f.exists() for f in korban_files)
    resp = await client.get("/api/v1/admin/jobs?email=korban@example.com")
    assert resp.json()["total"] == 0
    resp = await client.get("/api/v1/admin/jobs?email=lain@example.com")
    assert resp.json()["total"] == 1
    lain_file = Path(settings.upload_dir) / f"{lain_job_ids[0]}.png"
    assert lain_file.exists()

    # User tak dikenal -> 404.
    resp = await client.delete("/api/v1/admin/users/tidak-ada/jobs")
    assert resp.status_code == 404


# --- Toggle aktif/nonaktif akun (suspend) ---


async def test_admin_toggle_user_active(client, monkeypatch):
    """Suspend -> is_active false & login ditolak; reaktivasi -> login sukses lagi."""
    monkeypatch.setattr(settings, "admin_emails", ["boss@example.com"])
    await _register(client, "boss@example.com")
    await _register(client, "korban@example.com")

    # Login korban sukses saat aktif.
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "korban@example.com", "password": "password123"},
    )
    assert resp.status_code == 200

    await _login(client, "boss@example.com")
    korban_id = await _user_id_by_email(client, "korban@example.com")
    resp = await client.patch(
        f"/api/v1/admin/users/{korban_id}", json={"is_active": False}
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    # Login korban ditolak (403 — akun dinonaktifkan).
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "korban@example.com", "password": "password123"},
    )
    assert resp.status_code == 403

    # Reaktivasi -> login korban sukses lagi.
    resp = await client.patch(
        f"/api/v1/admin/users/{korban_id}", json={"is_active": True}
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "korban@example.com", "password": "password123"},
    )
    assert resp.status_code == 200


async def test_admin_suspend_kills_existing_session(client, monkeypatch):
    """Sesi yang sudah login langsung ditolak setelah suspend (get_current_user)."""
    monkeypatch.setattr(settings, "admin_emails", ["boss@example.com"])
    await _register(client, "boss@example.com")
    await _register(client, "korban@example.com")
    korban_token = client.cookies.get(settings.cookie_name)
    assert korban_token is not None
    assert (await client.get("/api/v1/auth/me")).status_code == 200

    await _login(client, "boss@example.com")
    korban_id = await _user_id_by_email(client, "korban@example.com")
    await client.patch(f"/api/v1/admin/users/{korban_id}", json={"is_active": False})

    # Pakai token korban yang lama -> 401.
    resp = await client.get(
        "/api/v1/auth/me", cookies={settings.cookie_name: korban_token}
    )
    assert resp.status_code == 401


async def test_admin_cannot_deactivate_self(client, monkeypatch):
    """Admin tidak bisa menonaktifkan akun sendiri (mengunci diri)."""
    monkeypatch.setattr(settings, "admin_emails", ["boss@example.com"])
    await _register(client, "boss@example.com")
    boss_id = await _user_id_by_email(client, "boss@example.com")

    resp = await client.patch(
        f"/api/v1/admin/users/{boss_id}", json={"is_active": False}
    )
    assert resp.status_code == 400
    # Mengaktifkan diri sendiri tetap boleh (no-op aman).
    resp = await client.patch(
        f"/api/v1/admin/users/{boss_id}", json={"is_active": True}
    )
    assert resp.status_code == 200


async def test_admin_toggle_unknown_and_denied(client, monkeypatch):
    """404 user tak dikenal; 403 utk user biasa."""
    monkeypatch.setattr(settings, "admin_emails", ["boss@example.com"])
    await _register(client, "boss@example.com")

    resp = await client.patch(
        "/api/v1/admin/users/tidak-ada", json={"is_active": False}
    )
    assert resp.status_code == 404

    await _register(client, "biasa@example.com")
    resp = await client.patch(
        "/api/v1/admin/users/some-id", json={"is_active": False}
    )
    assert resp.status_code == 403


# --- Ekspor CSV untuk audit (FR-13) ---


def _assert_csv(resp) -> None:
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]


async def test_admin_export_users_csv(client, monkeypatch):
    """CSV user: header + baris data + filter parsial email."""
    monkeypatch.setattr(settings, "admin_emails", ["boss@example.com"])
    await _register(client, "boss@example.com")
    await _register(client, "target@example.com")
    await _upload(client)  # 1 job atas nama target
    await _login(client, "boss@example.com")

    resp = await client.get("/api/v1/admin/export/users.csv")
    _assert_csv(resp)
    text = resp.text
    assert "email" in text and "jumlah_job" in text
    assert "target@example.com" in text

    # Filter parsial email.
    resp = await client.get("/api/v1/admin/export/users.csv?email=boss")
    _assert_csv(resp)
    assert "boss@example.com" in resp.text
    assert "target@example.com" not in resp.text


async def test_admin_export_jobs_csv(client, monkeypatch):
    """CSV job: email pemilik + nama file; filter email persis."""
    monkeypatch.setattr(settings, "admin_emails", ["boss@example.com"])
    await _register(client, "boss@example.com")
    await _register(client, "korban@example.com")
    await _upload(client)
    await _login(client, "boss@example.com")

    resp = await client.get("/api/v1/admin/export/jobs.csv?email=korban@example.com")
    _assert_csv(resp)
    text = resp.text
    assert "nama_file" in text
    assert "foto.png" in text
    assert "korban@example.com" in text

    # Tanpa filter = semua job.
    resp = await client.get("/api/v1/admin/export/jobs.csv")
    _assert_csv(resp)
    assert "foto.png" in resp.text


async def test_admin_export_transactions_csv(db, client, monkeypatch):
    """CSV transaksi kredit: order_id + email pemilik."""
    from datetime import UTC, datetime
    from uuid import uuid4

    from sqlalchemy import select

    from app.models.transaction import Transaction, TransactionStatus
    from app.models.user import User

    monkeypatch.setattr(settings, "admin_emails", ["boss@example.com"])
    await _register(client, "boss@example.com")
    await _register(client, "target@example.com")

    async with db() as session:
        target = (
            await session.execute(
                select(User).where(User.email == "target@example.com")
            )
        ).scalar_one()
        session.add(
            Transaction(
                id=str(uuid4()),
                user_id=target.id,
                order_id="ORD-CSV-001",
                package_slug="mini",
                amount_idr=20000,
                credits=25,
                status=TransactionStatus.PAID.value,
                created_at=datetime(2026, 8, 2, 3, 0, tzinfo=UTC),
                paid_at=datetime(2026, 8, 2, 3, 5, tzinfo=UTC),
            )
        )
        await session.commit()

    await _login(client, "boss@example.com")
    resp = await client.get("/api/v1/admin/export/transactions.csv")
    _assert_csv(resp)
    text = resp.text
    assert "order_id" in text
    assert "ORD-CSV-001" in text
    assert "target@example.com" in text


async def test_admin_export_denied_for_regular_user(client):
    """User biasa tidak boleh mengekspor CSV."""
    await _register(client)
    for kind in ("users", "jobs", "transactions"):
        resp = await client.get(f"/api/v1/admin/export/{kind}.csv")
        assert resp.status_code == 403


async def test_admin_user_transactions(db, client, monkeypatch):
    """Transaksi kredit milik satu user — terbaru dulu, tanpa bocor antar user."""
    from datetime import UTC, datetime
    from uuid import uuid4

    from sqlalchemy import select

    from app.models.transaction import Transaction, TransactionStatus
    from app.models.user import User

    monkeypatch.setattr(settings, "admin_emails", ["boss@example.com"])
    await _register(client, "boss@example.com")
    await _register(client, "target@example.com")
    await _register(client, "lain@example.com")

    # Dua transaksi milik target + satu milik user lain (tidak boleh bocor).
    async with db() as session:
        target = (
            await session.execute(
                select(User).where(User.email == "target@example.com")
            )
        ).scalar_one()
        lain = (
            await session.execute(
                select(User).where(User.email == "lain@example.com")
            )
        ).scalar_one()
        session.add_all(
            [
                Transaction(
                    id=str(uuid4()),
                    user_id=target.id,
                    order_id="ORD-T-002",
                    package_slug="mini",
                    amount_idr=20000,
                    credits=25,
                    status=TransactionStatus.PAID.value,
                    created_at=datetime(2026, 8, 2, 3, 0, tzinfo=UTC),
                    paid_at=datetime(2026, 8, 2, 3, 5, tzinfo=UTC),
                ),
                Transaction(
                    id=str(uuid4()),
                    user_id=target.id,
                    order_id="ORD-T-001",
                    package_slug="starter",
                    amount_idr=50000,
                    credits=70,
                    status=TransactionStatus.PENDING.value,
                    created_at=datetime(2026, 8, 1, 3, 0, tzinfo=UTC),
                ),
                Transaction(
                    id=str(uuid4()),
                    user_id=lain.id,
                    order_id="ORD-LAIN-001",
                    package_slug="mini",
                    amount_idr=20000,
                    credits=25,
                    status=TransactionStatus.PAID.value,
                    created_at=datetime(2026, 8, 1, 4, 0, tzinfo=UTC),
                ),
            ]
        )
        await session.commit()

    await _login(client, "boss@example.com")
    target_id = await _user_id_by_email(client, "target@example.com")
    resp = await client.get(f"/api/v1/admin/users/{target_id}/transactions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    # Terbaru dulu: ORD-T-002 (2 Agu) sebelum ORD-T-001 (1 Agu).
    assert [t["order_id"] for t in body["items"]] == ["ORD-T-002", "ORD-T-001"]
    paid = body["items"][0]
    assert paid["status"] == "paid"
    assert paid["credits"] == 25
    assert paid["amount_idr"] == 20000
    assert paid["paid_at"] is not None
    assert all(t["order_id"] != "ORD-LAIN-001" for t in body["items"])

    # 404 utk user tak dikenal.
    resp = await client.get("/api/v1/admin/users/tidak-ada/transactions")
    assert resp.status_code == 404
