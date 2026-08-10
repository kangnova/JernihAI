"""Tests pembayaran kredit (FR-11) — checkout, webhook, idempotensi,
upload memakai kredit saat kuota gratis habis, dan refund kredit."""

import hashlib
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
from app.models.job import Job, JobStatus
from app.models.transaction import Transaction, TransactionStatus

TEST_SERVER_KEY = "SB-Mid-server-test-abc123"


def _image_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 100, 50)).save(buf, format="PNG")
    return buf.getvalue()


def _signed_payload(order_id: str, *, status_code: str = "200") -> dict:
    """Buat payload notifikasi Midtrans dengan signature SHA512 yang valid."""
    gross = "10000"
    raw = f"{order_id}{status_code}{gross}{TEST_SERVER_KEY}"
    return {
        "order_id": order_id,
        "status_code": status_code,
        "gross_amount": gross,
        "transaction_status": "capture",
        "fraud_status": "accept",
        "transaction_id": "txn-123",
        "signature_key": hashlib.sha512(raw.encode()).hexdigest(),
    }


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
    # Dev tanpa gateway: checkout mengembalikan token mock.
    monkeypatch.setattr(settings, "midtrans_server_key", TEST_SERVER_KEY)
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


async def _register(client, email: str = "pay@example.com") -> str:
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "password123",
            "name": "Paya",
            "privacy_consent": True,
        },
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def _checkout(client, slug: str = "kredit-20") -> dict:
    resp = await client.post("/api/v1/billing/checkout", json={"package_slug": slug})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _pay(client, order_id: str) -> dict:
    resp = await client.post("/api/v1/billing/webhook", json=_signed_payload(order_id))
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _upload(client) -> dict:
    resp = await client.post(
        "/api/v1/jobs",
        files={"file": ("foto.png", _image_bytes(), "image/png")},
        data={"scale": "2", "output_format": "webp"},
    )
    return resp.json() if resp.status_code == 201 else resp


# --- packages & checkout ---


async def test_packages_requires_auth(client):
    resp = await client.get("/api/v1/billing/packages")
    assert resp.status_code == 401


async def test_packages_returns_balance_and_plans(client):
    await _register(client)
    resp = await client.get("/api/v1/billing/packages")
    assert resp.status_code == 200
    body = resp.json()
    assert body["credit_balance"] == 0
    slugs = {p["slug"] for p in body["packages"]}
    assert {"kredit-20", "lite-100", "pro-500"} <= slugs
    p20 = next(p for p in body["packages"] if p["slug"] == "kredit-20")
    assert p20["credits"] == 20 and p20["price_idr"] == 10_000


async def test_checkout_creates_pending_transaction(client, db):
    """Checkout tanpa konfigurasi gateway -> token mock + Transaction pending."""
    user_id = await _register(client)
    out = await _checkout(client)
    assert out["snap_token"].startswith("mock-")
    assert out["credits"] == 20
    assert out["amount_idr"] == 10_000

    async with db() as session:
        txn = (
            await session.execute(
                Transaction.__table__.select().where(
                    Transaction.order_id == out["order_id"]
                )
            )
        ).first()
        assert txn is not None
        assert txn.status == TransactionStatus.PENDING.value
        assert txn.user_id == user_id


async def test_checkout_unknown_package_404(client):
    await _register(client)
    resp = await client.post(
        "/api/v1/billing/checkout", json={"package_slug": "paket-misterius"}
    )
    assert resp.status_code == 404


# --- webhook ---


async def test_webhook_rejects_invalid_signature(client):
    await _register(client)
    out = await _checkout(client)
    payload = _signed_payload(out["order_id"])
    payload["signature_key"] = "0" * 128  # rusakkan signature

    resp = await client.post("/api/v1/billing/webhook", json=payload)
    assert resp.status_code == 403


async def test_webhook_paid_adds_credit_and_is_idempotent(client, db):
    """Notifikasi PAID mencairkan kredit; webhook duplikat tidak menggandakan."""
    user_id = await _register(client)
    out = await _checkout(client)

    first = await _pay(client, out["order_id"])
    assert first == {"status": "paid", "credits": "20"}

    # Duplikat (Midtrans mengulang notifikasi) -> diabaikan, tanpa top-up.
    duplicate = await _pay(client, out["order_id"])
    assert duplicate["status"] == "ignored"

    async with db() as session:
        from app.models.user import User

        user = await session.get(User, user_id)
        assert user.credit_balance == 20
        txn = await session.get(Transaction, (await session.execute(
            Transaction.__table__.select().where(
                Transaction.order_id == out["order_id"]
            )
        )).first().id)
        assert txn.status == TransactionStatus.PAID.value


async def test_webhook_unknown_order_ignored(client):
    await _register(client)
    resp = await client.post(
        "/api/v1/billing/webhook", json=_signed_payload("order-tidak-ada")
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


# --- upload memakai kredit (FR-11) ---


async def test_upload_uses_credit_when_free_quota_exhausted(client, db):
    """Kuota gratis habis -> upload berikutnya memakai 1 kredit."""
    await _register(client)
    for _ in range(3):  # habiskan kuota gratis (default 3/hari)
        assert (await _upload(client))["status"] == JobStatus.COMPLETED.value

    # Beli kredit via webhook.
    out = await _checkout(client)
    await _pay(client, out["order_id"])

    # Upload ke-4: gratis habis, pakai kredit.
    job = await _upload(client)
    assert job["status"] == JobStatus.COMPLETED.value

    quota = (await client.get("/api/v1/quota")).json()
    assert quota["remaining"] == 0
    assert quota["credit_balance"] == 19  # 20 - 1


async def test_failed_paid_job_refunds_credit(client, db, monkeypatch):
    """Job berbayar (kredit) yang gagal mengembalikan kredit (FR-11)."""
    user_id = await _register(client)
    out = await _checkout(client)
    await _pay(client, out["order_id"])

    # Simulasikan konsumsi 1 kredit saat job diterima (alur upload FR-11).
    async with db() as session:
        from app.models.user import User

        user = await session.get(User, user_id)
        user.credit_balance = 19
        await session.commit()

    # Buat job berbayar yang dijamin gagal (original tidak ada di disk).
    async with db() as session:
        session.add(
            Job(
                id="paid-fail",
                user_id=user_id,
                status=JobStatus.QUEUED.value,
                scale=2,
                output_format="webp",
                original_name="x.png",
                original_path=f"{settings.upload_dir}/ghost.png",
                uses_credit=True,
            )
        )
        await session.commit()

    status = await enhance_module.process_job("paid-fail")
    assert status == JobStatus.FAILED.value

    async with db() as session:
        from app.models.user import User

        user = await session.get(User, user_id)
        assert user.credit_balance == 20  # dikembalikan penuh
