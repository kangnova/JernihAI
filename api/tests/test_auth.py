"""Tests auth — register, login, me, logout (SQLite in-memory async)."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.session import get_db
from app.main import app
from app.models.base import Base

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture()
async def client(monkeypatch):
    # NFR-04: rate limiting nonaktif di test — test khusus memicunya sendiri.
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
    await engine.dispose()


async def test_register_sets_cookie_and_returns_user(client):
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "user@example.com",
            "password": "password123",
            "name": "Tono",
            "privacy_consent": True,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == "user@example.com"
    assert data["name"] == "Tono"
    assert data["provider"] == "local"
    assert "password" not in data
    assert resp.cookies.get("jernihai_session")


async def test_register_duplicate_email_conflicts(client):
    payload = {
        "email": "dup@example.com",
        "password": "password123",
        "name": "A",
        "privacy_consent": True,
    }
    await client.post("/api/v1/auth/register", json=payload)
    resp = await client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 409


async def test_register_requires_privacy_consent(client):
    """FR-07: register tanpa consent ditolak 422 (UU PDP)."""
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "noconsent@example.com", "password": "password123", "name": "T"},
    )
    assert resp.status_code == 422
    assert "privasi" in resp.json()["detail"]


async def test_register_records_consent_timestamp(client):
    """FR-07: consent tercatat saat register sukses."""
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "consent@example.com",
            "password": "password123",
            "name": "C",
            "privacy_consent": True,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["privacy_consent_at"] is not None


async def test_consent_endpoint_records_and_is_idempotent(client):
    """FR-07: endpoint consent dipakai user Google OAuth (tanpa form)."""
    # Simulasi user Google: register tanpa consent dulu ditolak, jadi buat
    # user lewat jalur yang mengizinkan (langsung insert via DB dipakai di
    # fixture lain); di sini kita pakai register + consent ulang idempoten.
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "g@example.com",
            "password": "password123",
            "name": "G",
            "privacy_consent": True,
        },
    )
    resp = await client.post("/api/v1/auth/consent")
    assert resp.status_code == 200
    assert resp.json()["privacy_consent_at"] is not None
    # Idempoten: panggilan kedua tetap sukses.
    resp = await client.post("/api/v1/auth/consent")
    assert resp.status_code == 200


async def test_consent_endpoint_requires_auth(client):
    resp = await client.post("/api/v1/auth/consent")
    assert resp.status_code == 401


async def test_login_ok(client):
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "login@example.com",
            "password": "password123",
            "name": "B",
            "privacy_consent": True,
        },
    )
    client.cookies.clear()
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "login@example.com", "password": "password123"},
    )
    assert resp.status_code == 200
    assert resp.cookies.get("jernihai_session")


async def test_login_wrong_password(client):
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "bad@example.com",
            "password": "password123",
            "name": "C",
            "privacy_consent": True,
        },
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "bad@example.com", "password": "wrongpass1"},
    )
    assert resp.status_code == 401


async def test_me_requires_auth(client):
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


async def test_me_returns_user_when_logged_in(client):
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "me@example.com",
            "password": "password123",
            "name": "D",
            "privacy_consent": True,
        },
    )
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    assert resp.json()["email"] == "me@example.com"


async def test_logout_clears_cookie(client):
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "out@example.com",
            "password": "password123",
            "name": "E",
            "privacy_consent": True,
        },
    )
    resp = await client.post("/api/v1/auth/logout")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_register_rate_limited_after_threshold(client, monkeypatch):
    """NFR-04: brute-force register dibatasi (429 setelah ambang)."""
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_auth_per_minute", 3)
    from app.core import ratelimit

    ratelimit.reset()
    for i in range(3):
        resp = await client.post(
            "/api/v1/auth/register",
            json={
                "email": f"rl{i}@example.com",
                "password": "password123",
                "name": "R",
                "privacy_consent": True,
            },
        )
        assert resp.status_code == 201
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "rl-extra@example.com",
            "password": "password123",
            "name": "R",
            "privacy_consent": True,
        },
    )
    assert resp.status_code == 429
    assert "coba lagi" in resp.json()["detail"]
