"""Tests hak subjek data (NFR-05 / UU PDP) — export & hapus akun."""

import io
from pathlib import Path

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
from app.models.job import Job


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


async def _register(client, email: str = "data@example.com") -> dict:
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "password123",
            "name": "Dina",
            "privacy_consent": True,
        },
    )
    assert resp.status_code == 201
    return resp.json()


async def _upload(client) -> dict:
    resp = await client.post(
        "/api/v1/jobs",
        files={"file": ("foto.png", _image_bytes(), "image/png")},
        data={"scale": "2", "output_format": "webp"},
    )
    assert resp.status_code == 201
    return resp.json()


async def test_export_requires_auth(client):
    resp = await client.get("/api/v1/account/export")
    assert resp.status_code == 401


async def test_export_contains_profile_and_jobs(client):
    """NFR-05: export memuat profil user + meta riwayat job."""
    await _register(client)
    job = await _upload(client)

    resp = await client.get("/api/v1/account/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert "attachment" in resp.headers.get("content-disposition", "")

    body = resp.json()
    assert body["user"]["email"] == "data@example.com"
    assert body["user"]["provider"] == "local"
    assert "password" not in str(body)  # tidak membocorkan hash
    assert body["free_quota"]["limit"] == 3
    assert len(body["jobs"]) == 1
    assert body["jobs"][0]["id"] == job["id"]
    assert body["jobs"][0]["original_name"] == "foto.png"


async def test_delete_account_requires_auth(client):
    resp = await client.delete("/api/v1/account")
    assert resp.status_code == 401


async def test_delete_account_removes_user_jobs_and_files(client, db):
    """NFR-05: hapus akun menghapus user, job rows, dan file di disk."""
    await _register(client)
    job = await _upload(client)
    # Path deterministik: save_upload menamai file `<job_id>.<ext>` dan
    # result `<job_id>.<format>`; di test folder-nya absolut (tmp_path).
    original_path = Path(settings.upload_dir) / f"{job['id']}.png"
    result_path = Path(settings.result_dir) / f"{job['id']}.webp"
    assert original_path.exists() and result_path.exists()

    resp = await client.delete("/api/v1/account")
    assert resp.status_code == 204
    assert not resp.cookies.get("jernihai_session")  # cookie dibersihkan

    # User & job hilang dari DB; file di disk ikut terhapus.
    async with db() as session:
        from sqlalchemy import func, select

        from app.models.user import User

        user_count = await session.scalar(
            select(func.count()).select_from(User).where(User.email == "data@example.com")
        )
        job_count = await session.scalar(
            select(func.count()).select_from(Job).where(Job.id == job["id"])
        )
    assert user_count == 0
    assert job_count == 0
    assert not result_path.exists()
    assert not original_path.exists()


async def test_delete_account_then_me_returns_401(client):
    """Setelah hapus akun, sesi tidak valid lagi."""
    await _register(client)
    await client.delete("/api/v1/account")
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401
