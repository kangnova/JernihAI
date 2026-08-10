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
