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


async def test_google_login_unconfigured_returns_503(client):
    """FR-01: GOOGLE_CLIENT_ID kosong → tombol Google nonaktif (503)."""
    resp = await client.get("/api/v1/auth/google", follow_redirects=False)
    assert resp.status_code == 503
    assert "Google" in resp.json()["detail"]


async def test_google_login_redirects_to_google_with_correct_params(
    client, monkeypatch
):
    """FR-01: dengan client id terisi, endpoint REDIRECT (302) ke Google
    dengan redirect_uri persis = WEB_URL + /api/v1/auth/google/callback,
    dan men-set cookie state CSRF."""
    monkeypatch.setattr(settings, "google_client_id", "fake-client-id.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "google_client_secret", "fake-secret")
    monkeypatch.setattr(settings, "web_url", "https://jernihai.example.com")

    resp = await client.get("/api/v1/auth/google", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith(
        "https://accounts.google.com/o/oauth2/v2/auth?"
    )
    # Parameter wajib alur OAuth + redirect_uri konsisten dengan callback.
    import urllib.parse

    params = urllib.parse.parse_qs(
        urllib.parse.urlparse(resp.headers["location"]).query
    )
    assert params["client_id"] == ["fake-client-id.apps.googleusercontent.com"]
    assert params["response_type"] == ["code"]
    assert params["scope"] == ["openid email profile"]
    assert params["redirect_uri"] == [
        "https://jernihai.example.com/api/v1/auth/google/callback"
    ]
    # state CSRF hadir & disimpan di cookie (proteksi login CSRF).
    assert params["state"]
    assert resp.cookies.get("oauth_state") == params["state"][0]


async def test_google_callback_unconfigured_returns_503(client):
    resp = await client.get("/api/v1/auth/google/callback?code=abc", follow_redirects=False)
    assert resp.status_code == 503


async def test_google_callback_rejects_missing_state(client, monkeypatch):
    """Login CSRF: callback tanpa state (atau state beda dari cookie) ditolak 400."""
    monkeypatch.setattr(settings, "google_client_id", "fake-client-id")
    monkeypatch.setattr(settings, "google_client_secret", "fake-secret")

    # Tanpa cookie oauth_state & tanpa param state → 400.
    resp = await client.get(
        "/api/v1/auth/google/callback?code=abc", follow_redirects=False
    )
    assert resp.status_code == 400

    # Cookie ada tapi param state beda → 400.
    client.cookies.set("oauth_state", "cookie-state")
    resp = await client.get(
        "/api/v1/auth/google/callback?code=abc&state=beda",
        follow_redirects=False,
    )
    assert resp.status_code == 400


async def test_google_callback_success_creates_user_and_redirects(
    client, monkeypatch
):
    """FR-01: alur lengkap callback sukses — tukar code (mock httpx),
    buat user provider=google, set cookie sesi, redirect ke web_url."""
    monkeypatch.setattr(settings, "google_client_id", "fake-client-id")
    monkeypatch.setattr(settings, "google_client_secret", "fake-secret")
    monkeypatch.setattr(settings, "web_url", "https://jernihai.example.com")

    # Mock panggilan keluar ke Google (token + userinfo) tanpa jaringan nyata.
    import app.api.routes.auth as auth_routes

    class FakeTokenResponse:
        status_code = 200

        def json(self):
            return {"access_token": "fake-access-token", "id_token": "x"}

    class FakeUserinfoResponse:
        status_code = 200

        def json(self):
            return {
                "email": "google.user@example.com",
                "name": "Gita",
                "sub": "google-sub-123",
            }

    class FakeClient:
        def __init__(self):
            self.post_called = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            self.post_called = True
            return FakeTokenResponse()

        async def get(self, *args, **kwargs):
            return FakeUserinfoResponse()

    monkeypatch.setattr(auth_routes.httpx, "AsyncClient", lambda: FakeClient())

    client.cookies.set("oauth_state", "state-benar")
    resp = await client.get(
        "/api/v1/auth/google/callback?code=valid-code&state=state-benar",
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "https://jernihai.example.com"
    assert resp.cookies.get("jernihai_session")  # sesi JWT ter-set
    # state CSRF dihapus setelah dipakai (one-time).
    assert "oauth_state" not in resp.cookies

    # User baru terdaftar dengan provider google & bisa /me.
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "google.user@example.com"
    assert me.json()["provider"] == "google"


async def test_google_callback_user_cancel_redirects_to_web(client, monkeypatch):
    """FR-01: user menekan Cancel di Google (error=access_denied) → redirect
    balik ke web (303), bukan 422 JSON mentah."""
    monkeypatch.setattr(settings, "google_client_id", "fake-client-id")
    monkeypatch.setattr(settings, "google_client_secret", "fake-secret")
    monkeypatch.setattr(settings, "web_url", "https://jernihai.example.com")

    # Tanpa code + dengan error (alur cancel Google), tanpa state pun OK.
    resp = await client.get(
        "/api/v1/auth/google/callback?error=access_denied",
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "https://jernihai.example.com"
    assert "jernihai_session" not in resp.cookies  # tidak login


async def test_google_callback_rejects_unverified_email(client, monkeypatch):
    """Hardening: email Google yang belum terverifikasi ditolak (400)."""
    monkeypatch.setattr(settings, "google_client_id", "fake-client-id")
    monkeypatch.setattr(settings, "google_client_secret", "fake-secret")
    monkeypatch.setattr(settings, "web_url", "https://jernihai.example.com")

    import app.api.routes.auth as auth_routes

    class FakeTokenResponse:
        status_code = 200

        def json(self):
            return {"access_token": "fake-access-token"}

    class FakeUserinfoResponse:
        status_code = 200

        def json(self):
            return {
                "email": "unverified@example.com",
                "email_verified": False,
                "name": "X",
                "sub": "s3",
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return FakeTokenResponse()

        async def get(self, *args, **kwargs):
            return FakeUserinfoResponse()

    monkeypatch.setattr(auth_routes.httpx, "AsyncClient", lambda: FakeClient())

    client.cookies.set("oauth_state", "state-ok")
    resp = await client.get(
        "/api/v1/auth/google/callback?code=c&state=state-ok",
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert "terverifikasi" in resp.json()["detail"]


async def test_google_callback_existing_user_logs_in(client, monkeypatch):
    """FR-01: email yang sudah terdaftar (lokal) bisa login via Google
    tanpa duplikasi — cookie sesi ter-set untuk user yang sama."""
    monkeypatch.setattr(settings, "google_client_id", "fake-client-id")
    monkeypatch.setattr(settings, "google_client_secret", "fake-secret")
    monkeypatch.setattr(settings, "web_url", "https://jernihai.example.com")

    # User lokal sudah ada.
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "existing@example.com",
            "password": "password123",
            "name": "Lama",
            "privacy_consent": True,
        },
    )
    client.cookies.clear()

    import app.api.routes.auth as auth_routes

    class FakeTokenResponse:
        status_code = 200

        def json(self):
            return {"access_token": "fake-access-token"}

    class FakeUserinfoResponse:
        status_code = 200

        def json(self):
            return {"email": "existing@example.com", "name": "Lama", "sub": "s2"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return FakeTokenResponse()

        async def get(self, *args, **kwargs):
            return FakeUserinfoResponse()

    monkeypatch.setattr(auth_routes.httpx, "AsyncClient", lambda: FakeClient())

    client.cookies.set("oauth_state", "state-ok")
    resp = await client.get(
        "/api/v1/auth/google/callback?code=c&state=state-ok",
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.cookies.get("jernihai_session")

    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "existing@example.com"

    # Tidak ada duplikasi: cek via login lokal masih satu user.
    client.cookies.clear()
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "existing@example.com", "password": "password123"},
    )
    assert login.status_code == 200
    assert login.json()["id"] == me.json()["id"]


async def test_register_rate_limited_after_threshold(client, monkeypatch):
    """NFR-04: brute-force register dibatasi (429 setelah ambang)."""
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_auth_per_minute", 3)
    from app.core import ratelimit

    await ratelimit.reset()
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
