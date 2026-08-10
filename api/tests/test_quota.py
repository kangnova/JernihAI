"""Tests kuota gratis harian (FR-06) — konsumsi, habis, reset WIB, refund.

Fixture sama dengan test_jobs.py: SQLite in-memory (StaticPool), pipeline
eager (mock backend), storage sementara.
"""

import io
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.core.quota as quota_module
import app.tasks.enhance as enhance_module
from app.core.config import settings
from app.db.session import get_db
from app.main import app
from app.models.base import Base


def _image_bytes(fmt: str = "png") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 100, 50)).save(buf, format=fmt.upper())
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


async def _quota(client) -> dict:
    resp = await client.get("/api/v1/quota")
    assert resp.status_code == 200
    return resp.json()


async def _upload(client) -> dict:
    resp = await client.post(
        "/api/v1/jobs",
        files={"file": ("foto.png", _image_bytes(), "image/png")},
        data={"scale": "2", "output_format": "webp"},
    )
    return resp


async def test_quota_endpoint_requires_auth(client):
    resp = await client.get("/api/v1/quota")
    assert resp.status_code == 401


async def test_quota_initial_full(client):
    await _register(client)
    data = await _quota(client)
    assert data == {
        "limit": 3,
        "used": 0,
        "remaining": 3,
        "reset_date": quota_module.wib_today().isoformat(),
        # FR-11: saldo kredit berbayar + total slot (gratis + kredit).
        "credit_balance": 0,
        "total_slots": 3,
    }


async def test_upload_consumes_quota(client):
    await _register(client)
    resp = await _upload(client)
    assert resp.status_code == 201
    assert (await _quota(client))["remaining"] == 2


async def test_quota_exhausted_after_three_uploads(client):
    await _register(client)
    for i in range(3):
        resp = await _upload(client)
        assert resp.status_code == 201, f"upload ke-{i + 1} seharusnya diterima"
    assert (await _quota(client))["remaining"] == 0

    # Upload ke-4 ditolak sebelum file dibaca (FR-06).
    resp = await _upload(client)
    assert resp.status_code == 403
    assert "Kuota gratis harian sudah habis" in resp.json()["detail"]


async def test_quota_resets_next_day(client, monkeypatch):
    await _register(client)
    for _ in range(3):
        await _upload(client)
    assert (await _quota(client))["remaining"] == 0

    # Berpura-pura sudah hari berikutnya (WIB) -> kuota reset penuh.
    next_day = quota_module.wib_today() + timedelta(days=1)
    monkeypatch.setattr(quota_module, "wib_today", lambda: next_day)

    data = await _quota(client)
    assert data["remaining"] == 3
    assert data["used"] == 0
    # Upload pun kembali bisa.
    resp = await _upload(client)
    assert resp.status_code == 201


async def test_failed_job_refunds_quota(client, db, monkeypatch):
    """Job gagal tidak menghabiskan kuota (refund di process_job)."""
    await _register(client)
    await _upload(client)  # sukses -> used 1, remaining 2
    assert (await _quota(client))["remaining"] == 2

    # Upload berikutnya gagal di pipeline (backend tak dikenal).
    monkeypatch.setattr(settings, "enhance_backend", "garbage")
    resp = await _upload(client)
    assert resp.status_code == 201
    assert resp.json()["status"] == "failed"

    # Kuota di-refund: tetap 2 (bukan 1).
    assert (await _quota(client))["remaining"] == 2
