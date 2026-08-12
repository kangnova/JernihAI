"""Tests health check (NFR-08) — liveness & readiness dengan dependensi nyata.

Pola: sama dengan suite lain (httpx ASGITransport). Engine DB di-monkeypatch
ke sqlite in-memory (sehat) atau path tak valid (rusak) agar test tidak
bergantung pada Postgres yang berjalan. Redis tidak tersedia di test →
mode eager melewati cek (skipped), atau URL sengaja ditunjuk ke port mati
untuk memicu kegagalan.
"""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

import app.db.session as db_session
from app.core.config import settings
from app.main import app
from app.models.base import Base
from app.models.job import Job, JobStatus


@pytest.fixture()
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture()
async def healthy_db(monkeypatch):
    """DB sehat (sqlite in-memory, tabel lengkap) + mode eager -> Redis
    dilewati (skipped). Tabel dibuat agar endpoint metrik (query jobs)
    berfungsi — readiness hanya SELECT 1, tidak butuh tabel."""
    monkeypatch.setattr(settings, "celery_task_always_eager", True)
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    monkeypatch.setattr(db_session, "engine", engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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
    assert "/api/v1/health/metrics" in resp.json()["paths"]


# --- Metrik operasional (NFR-08) ---


async def test_metrics_ok_keys(client, healthy_db):
    """Metrik: 200 + struktur lengkap; mode eager -> antrean skipped."""
    resp = await client.get("/api/v1/health/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["service"] == "JernihAI API"
    assert data["queue"] == {
        "status": "skipped",
        "length": None,
        "detail": "mode eager (tanpa broker)",
    }
    assert set(data["jobs"]) == {"queued", "processing", "completed", "failed"}
    assert set(data["throughput"]) == {
        "completed_1h", "completed_24h", "failed_24h", "failure_rate_24h"
    }
    assert set(data["latency"]) == {"avg_processing_seconds_24h", "samples"}
    assert data["config"]["storage_backend"] == "local"


async def test_metrics_job_counts_and_latency(client, monkeypatch):
    """Metrik merefleksikan job nyata: hitungan per status, throughput
    jendela, failure rate, dan rata-rata durasi proses."""
    monkeypatch.setattr(settings, "celery_task_always_eager", True)
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    monkeypatch.setattr(db_session, "engine", engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        now = datetime.now(UTC)
        async with AsyncSession(bind=engine) as session:
            session.add_all(
                [
                    Job(
                        id="m-q", user_id="u", status=JobStatus.QUEUED.value,
                        original_name="a.png", original_path="s/a.png",
                        created_at=now, updated_at=now,
                    ),
                    Job(
                        id="m-p", user_id="u", status=JobStatus.PROCESSING.value,
                        original_name="b.png", original_path="s/b.png",
                        created_at=now, updated_at=now,
                    ),
                    # Selesai 10 menit lalu (dalam jendela 1 jam & 24 jam).
                    Job(
                        id="m-c1", user_id="u", status=JobStatus.COMPLETED.value,
                        original_name="c.png", original_path="s/c.png",
                        result_path="s/r/c.webp",
                        created_at=now - timedelta(minutes=30),
                        finished_at=now - timedelta(minutes=10),
                        updated_at=now - timedelta(minutes=10),
                    ),
                    # Selesai 10 jam lalu (dalam 24 jam, di luar 1 jam).
                    Job(
                        id="m-c2", user_id="u", status=JobStatus.COMPLETED.value,
                        original_name="d.png", original_path="s/d.png",
                        result_path="s/r/d.webp",
                        created_at=now - timedelta(hours=20),
                        finished_at=now - timedelta(hours=10),
                        updated_at=now - timedelta(hours=10),
                    ),
                    # Gagal 4 jam lalu (updated_at dalam 24 jam).
                    Job(
                        id="m-f", user_id="u", status=JobStatus.FAILED.value,
                        original_name="e.png", original_path="s/e.png",
                        error="gagal uji",
                        created_at=now - timedelta(hours=5),
                        updated_at=now - timedelta(hours=4),
                    ),
                ]
            )
            await session.commit()

        resp = await client.get("/api/v1/health/metrics")
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    data = resp.json()
    assert data["jobs"] == {"queued": 1, "processing": 1, "completed": 2, "failed": 1}
    tp = data["throughput"]
    assert tp["completed_1h"] == 1  # hanya m-c1
    assert tp["completed_24h"] == 2
    assert tp["failed_24h"] == 1
    assert tp["failure_rate_24h"] == pytest.approx(1 / 3, abs=1e-3)
    # Latensi rata-rata: (20 mnt + 10 jam) / 2 = 18600 detik.
    lat = data["latency"]
    assert lat["samples"] == 2
    assert lat["avg_processing_seconds_24h"] == pytest.approx(18600.0)


async def test_metrics_queue_error_when_redis_down(client, healthy_db, monkeypatch):
    """Broker aktif tapi Redis unreachable -> queue.status=error, endpoint 200."""
    monkeypatch.setattr(settings, "celery_task_always_eager", False)
    monkeypatch.setattr(settings, "celery_broker_url", "redis://127.0.0.1:1/0")

    resp = await client.get("/api/v1/health/metrics")

    assert resp.status_code == 200  # metrik opsional tidak boleh 500
    assert resp.json()["queue"]["status"] == "error"
