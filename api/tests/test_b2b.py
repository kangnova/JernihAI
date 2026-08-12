"""Tests API Publik B2B (FR-14) — autentikasi via X-API-Key, pay-per-call.

Cakupan: manajemen key (buat/lihat/cabut), alur job via key (upload ->
status -> unduh, 1 kredit terpotong), penolakan key tak valid/dicabut,
402 saat saldo kosong, isolasi antar key, rate limit per tier.
"""

import io

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.tasks.enhance as enhance_module
from app.core.config import settings
from app.core.ratelimit import reset as rate_limit_reset
from app.db.session import get_db
from app.main import app
from app.models.base import Base
from app.models.user import User


def _image_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (120, 60, 200)).save(buf, format="PNG")
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
    monkeypatch.setattr(settings, "free_daily_quota", 3)
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


async def _register(client, email: str = "dev@example.com") -> None:
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "password123",
            "name": "Developer",
            "privacy_consent": True,
        },
    )
    assert resp.status_code == 201


async def _create_key(client, name: str = "Produksi", tier: str = "free") -> str:
    """Buat key atas nama user yang sedang login; return full key."""
    resp = await client.post(
        "/api/v1/b2b/keys", json={"name": name, "tier": tier}
    )
    assert resp.status_code == 201
    return resp.json()["full_key"]


# --- Manajemen key ---


async def test_api_key_management_flow(client):
    """Buat -> list (tersamar, tanpa key asli) -> cabut -> nonaktif."""
    await _register(client)
    full_key = await _create_key(client, "Produksi", "pro")
    assert full_key.startswith("jn_")

    resp = await client.get("/api/v1/b2b/keys")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["name"] == "Produksi"
    assert item["tier"] == "pro"
    assert item["is_active"] is True
    assert full_key.startswith(item["key_prefix"])  # prefix sesuai
    assert full_key not in str(body)  # key asli TIDAK pernah bocor di list

    resp = await client.delete(f"/api/v1/b2b/keys/{item['id']}")
    assert resp.status_code == 204
    resp = await client.get("/api/v1/b2b/keys")
    assert resp.json()["items"][0]["is_active"] is False


async def test_api_key_requires_login_and_validation(client):
    resp = await client.get("/api/v1/b2b/keys")
    assert resp.status_code == 401
    await _register(client)
    resp = await client.post("/api/v1/b2b/keys", json={"name": "", "tier": "free"})
    assert resp.status_code == 422
    resp = await client.post(
        "/api/v1/b2b/keys", json={"name": "X", "tier": "ultra"}
    )
    assert resp.status_code == 400


async def test_revoke_other_users_key_404(client):
    """Tidak bisa mencabut key milik user lain (404, tanpa bocor info)."""
    await _register(client, "a@example.com")
    await _create_key(client, "Key-A")
    key_a_id = (await client.get("/api/v1/b2b/keys")).json()["items"][0]["id"]

    await _register(client, "b@example.com")  # cookie pindah ke B
    resp = await client.delete(f"/api/v1/b2b/keys/{key_a_id}")
    assert resp.status_code == 404
    # B tidak melihat key milik A.
    assert (await client.get("/api/v1/b2b/keys")).json()["total"] == 0

    # Key A tetap aktif (dicabut oleh B tadi gagal).
    resp = await client.delete("/api/v1/b2b/keys/tidak-ada")
    assert resp.status_code == 404


# --- Alur job B2B via API key ---


async def _set_credit(client, email: str, amount: int, db) -> None:
    from sqlalchemy import select

    async with db() as session:
        user = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one()
        user.credit_balance = amount
        await session.commit()


async def test_b2b_job_flow_pay_per_call(db, client, monkeypatch):
    """Pay-per-call: upload sukses -> completed; 1 kredit terpotong dari saldo."""
    await _register(client)
    await _set_credit(client, "dev@example.com", 5, db)
    full_key = await _create_key(client)

    resp = await client.post(
        "/api/v1/b2b/jobs",
        headers={"X-API-Key": full_key},
        files={"file": ("foto.png", _image_bytes(), "image/png")},
        data={"scale": "2", "output_format": "webp"},
    )
    assert resp.status_code == 201
    job = resp.json()
    assert job["status"] == "completed"
    job_id = job["id"]

    # Status via key.
    resp = await client.get(
        f"/api/v1/b2b/jobs/{job_id}", headers={"X-API-Key": full_key}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"

    # Unduh hasil.
    resp = await client.get(
        f"/api/v1/b2b/jobs/{job_id}/result", headers={"X-API-Key": full_key}
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/")
    assert resp.content

    # Saldo turun tepat 1 kredit.
    resp = await client.get("/api/v1/b2b/quota", headers={"X-API-Key": full_key})
    assert resp.status_code == 200
    assert resp.json()["credit_balance"] == 4


async def test_b2b_no_credit_402(db, client, monkeypatch):
    """Saldo kredit kosong -> 402 Payment Required, job tidak dibuat."""
    await _register(client)
    await _set_credit(client, "dev@example.com", 0, db)
    full_key = await _create_key(client)

    resp = await client.post(
        "/api/v1/b2b/jobs",
        headers={"X-API-Key": full_key},
        files={"file": ("foto.png", _image_bytes(), "image/png")},
    )
    assert resp.status_code == 402


async def test_b2b_png_oversize_rejected_credit_untouched(db, client, monkeypatch):
    """ADR-004: PNG > 4096 px ditolak 400 via B2B — kredit TIDAK terpotong."""
    await _register(client)
    await _set_credit(client, "dev@example.com", 5, db)
    full_key = await _create_key(client)

    buf = io.BytesIO()
    Image.new("RGB", (4097, 1), (10, 20, 30)).save(buf, format="PNG")

    resp = await client.post(
        "/api/v1/b2b/jobs",
        headers={"X-API-Key": full_key},
        files={"file": ("huge.png", buf.getvalue(), "image/png")},
        data={"scale": "2", "output_format": "png"},
    )
    assert resp.status_code == 400
    assert "4096" in resp.json()["detail"]

    # Saldo utuh — penolakan terjadi SEBELUM potongan kredit.
    resp = await client.get("/api/v1/b2b/quota", headers={"X-API-Key": full_key})
    assert resp.status_code == 200
    assert resp.json()["credit_balance"] == 5


async def test_b2b_failed_job_refunds_credit(db, client, monkeypatch):
    """Job B2B yang gagal -> 1 kredit di-refund otomatis ke saldo pemilik.

    Fondasi model pay-per-call: user tidak dirugikan saat pipeline gagal
    (sama dengan alur kredit FR-11; lihat app/tasks/enhance.py process_job
    `refund_on_fail` + `job.uses_credit`).
    """
    await _register(client)
    await _set_credit(client, "dev@example.com", 5, db)
    full_key = await _create_key(client)

    def boom(job):
        raise RuntimeError("pipeline gagal (simulasi)")

    monkeypatch.setattr(enhance_module, "_enhance", boom)

    resp = await client.post(
        "/api/v1/b2b/jobs",
        headers={"X-API-Key": full_key},
        files={"file": ("foto.png", _image_bytes(), "image/png")},
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "failed"

    # Saldo kembali penuh: 5 (tidak terpotong oleh job yang gagal).
    resp = await client.get("/api/v1/b2b/quota", headers={"X-API-Key": full_key})
    assert resp.status_code == 200
    assert resp.json()["credit_balance"] == 5


# --- Keamanan: key valid, isolasi, rate limit ---


async def test_b2b_requires_valid_key(client):
    await _register(client)
    resp = await client.post(
        "/api/v1/b2b/jobs",
        files={"file": ("foto.png", _image_bytes(), "image/png")},
    )
    assert resp.status_code == 401  # tanpa header

    resp = await client.post(
        "/api/v1/b2b/jobs",
        headers={"X-API-Key": "jn_salah-salah"},
        files={"file": ("foto.png", _image_bytes(), "image/png")},
    )
    assert resp.status_code == 401  # key tidak dikenal


async def test_b2b_revoked_key_denied(client):
    await _register(client)
    full_key = await _create_key(client)
    key_id = (await client.get("/api/v1/b2b/keys")).json()["items"][0]["id"]
    await client.delete(f"/api/v1/b2b/keys/{key_id}")

    resp = await client.get(
        "/api/v1/b2b/quota", headers={"X-API-Key": full_key}
    )
    assert resp.status_code == 403


async def test_b2b_job_isolation_between_keys(db, client):
    """Key user A tidak bisa melihat job milik user B (404 tanpa bocor)."""
    await _register(client, "a@example.com")
    await _set_credit(client, "a@example.com", 3, db)
    key_a = await _create_key(client)

    resp = await client.post(
        "/api/v1/b2b/jobs",
        headers={"X-API-Key": key_a},
        files={"file": ("foto.png", _image_bytes(), "image/png")},
    )
    job_a = resp.json()["id"]

    await _register(client, "b@example.com")
    key_b = await _create_key(client)

    resp = await client.get(
        f"/api/v1/b2b/jobs/{job_a}", headers={"X-API-Key": key_b}
    )
    assert resp.status_code == 404
    resp = await client.get(
        f"/api/v1/b2b/jobs/{job_a}/result", headers={"X-API-Key": key_b}
    )
    assert resp.status_code == 404


async def test_b2b_rate_limit_per_tier(client, monkeypatch):
    """Tier free dibatasi lebih ketat dari pro (NFR-04 / FR-14)."""
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "api_rate_limit_free_per_minute", 2)
    monkeypatch.setattr(settings, "api_rate_limit_pro_per_minute", 3)
    await rate_limit_reset()

    await _register(client)
    key_free = await _create_key(client, "free-key", "free")
    key_pro = await _create_key(client, "pro-key", "pro")

    # Tier free: 2 request sukses, request ke-3 -> 429.
    for _ in range(2):
        resp = await client.get("/api/v1/b2b/quota", headers={"X-API-Key": key_free})
        assert resp.status_code == 200
    resp = await client.get("/api/v1/b2b/quota", headers={"X-API-Key": key_free})
    assert resp.status_code == 429

    # Tier pro: limit 3 — 3 request sukses (key berbeda, counter terpisah).
    await rate_limit_reset()
    for _ in range(3):
        resp = await client.get("/api/v1/b2b/quota", headers={"X-API-Key": key_pro})
        assert resp.status_code == 200
    resp = await client.get("/api/v1/b2b/quota", headers={"X-API-Key": key_pro})
    assert resp.status_code == 429

    await rate_limit_reset()
