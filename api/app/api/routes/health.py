"""Endpoint health check — liveness & readiness (NFR-08).

- `/health` (liveness): proses hidup — tanpa dependensi.
- `/health/ready` (readiness): memverifikasi dependensi WAJIB sebelum
  traffic dialihkan (orchestrator/load balancer):
  - PostgreSQL: `SELECT 1` lewat engine aplikasi (`app.db.session.engine`).
  - Redis: `PING` — hanya diwajibkan saat broker aktif. Mode eager
    (`celery_task_always_eager=True`, dev/test tanpa Redis) melewati
    cek ini (status "skipped") — readiness tidak boleh gagal karena
    dependensi yang memang tidak dipakai.

Readiness selalu menanggapi cepat: tiap cek dibatasi timeout 2 detik
(host unreachable tidak boleh menggantung probe — asyncpg/redis punya
timeout bawaan yang bisa puluhan detik).
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Response, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.job import Job, JobStatus
from app.schemas.health import (
    CheckResult,
    ConfigInfo,
    HealthResponse,
    JobCounts,
    LatencyMetric,
    MetricsResponse,
    QueueMetric,
    ThroughputMetric,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

# NFR-08: probe harus selesai cepat agar orchestrator bisa menilai
# readiness tanpa menumpuk koneksi.
CHECK_TIMEOUT_SECONDS = 2.0


def _generic_fail(name: str) -> CheckResult:
    """Respons gagal dengan detail GENERIK — probe readiness tidak
    berautentikasi; detail koneksi asli (host/URI) cukup di log server."""
    return CheckResult(name=name, status="fail", detail="tidak dapat terhubung")


async def _check_db() -> CheckResult:
    """SELECT 1 ke database aplikasi (engine sesi nyata)."""
    from app.db import session  # lazy: engine dibuat sekali di session.py

    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            async with session.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        return CheckResult(name="postgres", status="ok")
    except Exception as exc:  # noqa: BLE001 — ringkas semua mode gagal
        logger.warning("Health check DB gagal: %s", exc)
        return _generic_fail("postgres")


async def _check_redis() -> CheckResult:
    """PING Redis BROKER — wajib hanya saat broker aktif (bukan mode eager).

    Yang dicek `celery_broker_url` (queue job), bukan `redis_url` yang tak
    terpakai — readiness harus mencerminkan dependensi yang benar-benar
    dibutuhkan worker untuk memproses job.
    """
    if settings.celery_task_always_eager:
        return CheckResult(
            name="redis",
            status="skipped",
            detail="mode eager (tanpa broker)",
        )
    import redis.asyncio as aioredis

    client = None
    try:
        client = aioredis.from_url(
            settings.celery_broker_url,
            socket_connect_timeout=CHECK_TIMEOUT_SECONDS,
        )
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            await client.ping()
        return CheckResult(name="redis", status="ok")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Health check Redis gagal: %s", exc)
        return _generic_fail("redis")
    finally:
        if client is not None:
            await client.aclose()


def _as_utc_naive(dt: datetime | None) -> datetime | None:
    """Normalisasi tz untuk selisih waktu yang aman lintas DB.

    SQLite mengembalikan datetime NAIVE (tz tak tersimpan), Postgres AWARE —
    mengurangkan keduanya campur aduk bisa TypeError. Selisih dalam UTC
    sama untuk keduanya, jadi cukup buang tzinfo.
    """
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


async def _queue_metric() -> QueueMetric:
    """Panjang antrean Celery via Redis `LLEN celery` — sinyal autoscale.

    Bukan cek readiness: Redis tidak terjangkau => status "error" (bukan
    500) — endpoint metrik TIDAK boleh ikut down karena dependensi opsional.
    """
    if settings.celery_task_always_eager:
        return QueueMetric(status="skipped", detail="mode eager (tanpa broker)")
    import redis.asyncio as aioredis

    client = None
    try:
        client = aioredis.from_url(
            settings.celery_broker_url,
            socket_connect_timeout=CHECK_TIMEOUT_SECONDS,
        )
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            length = await client.llen("celery")
        return QueueMetric(status="ok", length=int(length or 0))
    except Exception as exc:  # noqa: BLE001 — metrik opsional, jangan 500
        logger.warning("Metrics: baca antrean Redis gagal: %s", exc)
        return QueueMetric(status="error", detail="tidak dapat membaca antrean")
    finally:
        if client is not None:
            await client.aclose()


async def _job_counts(session: AsyncSession) -> JobCounts:
    """Snapshot jumlah job per status saat ini."""
    rows = await session.execute(select(Job.status, func.count()).group_by(Job.status))
    counts = {status: n for status, n in rows.all()}
    return JobCounts(
        queued=counts.get(JobStatus.QUEUED.value, 0),
        processing=counts.get(JobStatus.PROCESSING.value, 0),
        completed=counts.get(JobStatus.COMPLETED.value, 0),
        failed=counts.get(JobStatus.FAILED.value, 0),
    )


async def _throughput(session: AsyncSession) -> ThroughputMetric:
    """Job selesai/gagal pada jendela 1 jam & 24 jam (anchor `updated_at`).

    `updated_at` dipakai karena di-set pada setiap transisi status (onupdate);
    `finished_at` sengaja kosong untuk job gagal (kontrak ADR-012) sehingga
    tidak bisa jadi anchor tunggal untuk keduanya.
    """
    now = datetime.now(UTC)
    h1 = now - timedelta(hours=1)
    h24 = now - timedelta(hours=24)

    async def _count(status: str, since: datetime) -> int:
        return (
            await session.scalar(
                select(func.count())
                .select_from(Job)
                .where(Job.status == status, Job.updated_at >= since)
            )
            or 0
        )

    completed_1h = await _count(JobStatus.COMPLETED.value, h1)
    completed_24h = await _count(JobStatus.COMPLETED.value, h24)
    failed_24h = await _count(JobStatus.FAILED.value, h24)
    total_24h = completed_24h + failed_24h
    return ThroughputMetric(
        completed_1h=completed_1h,
        completed_24h=completed_24h,
        failed_24h=failed_24h,
        failure_rate_24h=(
            round(failed_24h / total_24h, 4) if total_24h else None
        ),
    )


async def _latency(session: AsyncSession) -> LatencyMetric:
    """Rata-rata durasi (created_at -> finished_at) job completed 24 jam.

    Dihitung di Python dari sampel (maks 500) — `AVG(interval)` tidak
    portabel lintas SQLite/Postgres. Verifikasi KPI NFR-01 end-to-end.
    """
    now = datetime.now(UTC)
    h24 = now - timedelta(hours=24)
    rows = await session.execute(
        select(Job.created_at, Job.finished_at)
        .where(Job.status == JobStatus.COMPLETED.value, Job.finished_at >= h24)
        .order_by(Job.finished_at.desc())  # sampel = 500 TERBARU (deterministik)
        .limit(500)
    )
    durations: list[float] = []
    for created, finished in rows.all():
        c, f = _as_utc_naive(created), _as_utc_naive(finished)
        if c is not None and f is not None and f >= c:
            durations.append((f - c).total_seconds())
    if not durations:
        return LatencyMetric(avg_processing_seconds_24h=None, samples=0)
    return LatencyMetric(
        avg_processing_seconds_24h=round(sum(durations) / len(durations), 1),
        samples=len(durations),
    )


@router.get(
    "/health/metrics",
    response_model=MetricsResponse,
    summary="Metrik operasional untuk pemantauan & autoscale (NFR-08)",
)
async def metrics() -> MetricsResponse:
    """Metrik ringan untuk keputusan operasional (dipakai `queue_monitor.py`):

    - `queue.length` — antrean Celery dari Redis (LLEN): menumpuk terus =
      tambah worker (autoscale up); nyaris nol = kapasitas berlebih.
    - `jobs.*` — snapshot jumlah job per status.
    - `throughput.*` — laju selesai/gagal 1 & 24 jam + failure rate.
    - `latency.*` — rata-rata durasi proses (KPI NFR-01 end-to-end).
    - `config.*` — konteks (storage/rate limit/enhance backend, environment).

    Tanpa autentikasi (seperti /health) — di produksi batasi aksesnya di
    gateway/firewall; endpoint ini tetap 200 walau Redis tidak terjangkau.
    """
    from app.db import session as db_session

    queue = await _queue_metric()
    async with AsyncSession(bind=db_session.engine) as session:
        jobs = await _job_counts(session)
        throughput = await _throughput(session)
        latency = await _latency(session)
    return MetricsResponse(
        service=settings.app_name,
        version=settings.app_version,
        generated_at=datetime.now(UTC),
        queue=queue,
        jobs=jobs,
        throughput=throughput,
        latency=latency,
        config=ConfigInfo(
            environment=settings.environment,
            storage_backend=settings.storage_backend,
            rate_limit_backend=settings.rate_limit_backend,
            enhance_backend=settings.enhance_backend,
        ),
    )


@router.get("/health", response_model=HealthResponse, summary="Liveness check")
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
    )


@router.get("/health/ready", response_model=HealthResponse, summary="Readiness check")
async def ready(response: Response) -> HealthResponse:
    checks = await asyncio.gather(_check_db(), _check_redis())
    failed = [c.name for c in checks if c.status == "fail"]
    if failed:
        # 503 = belum siap menerima traffic (NFR-08) — tubuh respons tetap
        # membawa detail cek agar debugging mudah.
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="degraded" if failed else "ok",
        service=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
        checks=list(checks),
    )
