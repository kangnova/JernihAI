"""Tests health check (NFR-08) — liveness & readiness dengan dependensi nyata.

Pola: sama dengan suite lain (httpx ASGITransport). Engine DB di-monkeypatch
ke sqlite in-memory (sehat) atau path tak valid (rusak) agar test tidak
bergantung pada Postgres yang berjalan. Redis tidak tersedia di test →
mode eager melewati cek (skipped), atau URL sengaja ditunjuk ke port mati
untuk memicu kegagalan.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

import app.db.session as db_session
from app.core.config import settings
from app.main import app


@pytest.fixture()
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture()
async def healthy_db(monkeypatch):
    """DB sehat (sqlite in-memory) + mode eager -> Redis dilewati (skipped)."""
    monkeypatch.setattr(settings, "celery_task_always_eager", True)
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    monkeypatch.setattr(db_session, "engine", engine)
    yield
    await engine.dispose()


async def test_health_liveness_ok(client):
    """Liveness: proses hidup, tanpa dependensi -> selalu 200 ok."""
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "JernihAI API"


async def test_health_ready_ok(client, healthy_db):
    """DB sehat + mode eager (Redis skipped) -> 200 ok, detail per cek."""
    resp = await client.get("/api/v1/health/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    checks = {c["name"]: c["status"] for c in data["checks"]}
    assert checks == {"postgres": "ok", "redis": "skipped"}


async def test_health_ready_db_down_503(client, monkeypatch):
    """DB unreachable -> 503 degraded (orchestrator menarik traffic)."""
    monkeypatch.setattr(settings, "celery_task_always_eager", True)
    engine = create_async_engine("sqlite+aiosqlite:///no/such/dir/jernihai.db")
    monkeypatch.setattr(db_session, "engine", engine)
    try:
        resp = await client.get("/api/v1/health/ready")
    finally:
        await engine.dispose()

    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "degraded"
    checks = {c["name"]: c["status"] for c in data["checks"]}
    assert checks["postgres"] == "fail"
    assert checks["redis"] == "skipped"  # eager: tidak ikut menilai


async def test_health_ready_redis_down_503(client, healthy_db, monkeypatch):
    """Broker aktif tapi Redis unreachable -> 503, walau DB sehat."""
    monkeypatch.setattr(settings, "celery_task_always_eager", False)
    # Port 1 lokal: koneksi ditolak cepat (deterministik tanpa Redis asli).
    monkeypatch.setattr(settings, "celery_broker_url", "redis://127.0.0.1:1/0")

    resp = await client.get("/api/v1/health/ready")

    assert resp.status_code == 503
    checks = {c["name"]: c["status"] for c in resp.json()["checks"]}
    assert checks["postgres"] == "ok"
    assert checks["redis"] == "fail"


async def test_openapi_exposes_health(client) -> None:
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    assert "/api/v1/health" in resp.json()["paths"]
    assert "/api/v1/health/ready" in resp.json()["paths"]
