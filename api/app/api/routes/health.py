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

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.core.config import settings
from app.schemas.health import CheckResult, HealthResponse

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
