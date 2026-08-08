"""Tests auth — register, login, me, logout (SQLite in-memory async)."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.session import get_db
from app.main import app
from app.models.base import Base

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture()
async def client():
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
        json={"email": "user@example.com", "password": "password123", "name": "Tono"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == "user@example.com"
    assert data["name"] == "Tono"
    assert data["provider"] == "local"
    assert "password" not in data
    assert resp.cookies.get("jernihai_session")


async def test_register_duplicate_email_conflicts(client):
    payload = {"email": "dup@example.com", "password": "password123", "name": "A"}
    await client.post("/api/v1/auth/register", json=payload)
    resp = await client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 409


async def test_login_ok(client):
    await client.post(
        "/api/v1/auth/register",
        json={"email": "login@example.com", "password": "password123", "name": "B"},
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
        json={"email": "bad@example.com", "password": "password123", "name": "C"},
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
        json={"email": "me@example.com", "password": "password123", "name": "D"},
    )
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    assert resp.json()["email"] == "me@example.com"


async def test_logout_clears_cookie(client):
    await client.post(
        "/api/v1/auth/register",
        json={"email": "out@example.com", "password": "password123", "name": "E"},
    )
    resp = await client.post("/api/v1/auth/logout")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
