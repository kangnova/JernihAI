"""Rate limiting (NFR-04) — backend in-memory (dev/test) atau Redis (produksi).

Fixed-window counter per (key, scope). Dipakai sebagai FastAPI dependency
pada endpoint sensitif (auth, upload, API B2B).

Backend (dipilih via `settings.rate_limit_backend`):
- `memory` (default): dict + lock — cukup untuk dev/test & single-instance,
  tanpa dependency eksternal. State per proses.
- `redis`: counter INCR + EXPIRE atomic (pipeline) di `settings.redis_url` —
  state DIBAGI antar instance (Fase 3 multi-node / autoscale).

API publik sama untuk kedua backend:
- `check(...)` — tolak (429) bila melebihi limit. Kini async (Redis I/O).
- `reset()` — kosongkan semua counter (dipakai test / reset manual).
- `rate_limit_dependency(...)` — FastAPI dependency.

Catatan trade-off (fixed-window):
- Serangan bisa ~2× limit dengan membelah batas jendela (`:59` + `:00`)
  — trade-off fixed-window yang wajar untuk NFR-04, bukan sliding window.
- Backend redis meng-INCR juga pada request yang ditolak (counter terus
  naik saat 429), memory tidak — efek blokir sama, hanya pembacaan counter
  yang sedikit berbeda.
- Redis down = FAIL-OPEN (request dilewati + log warning), bukan 500 —
  health check `/health/ready` yang melaporkan Redis tidak sehat.

Test menonaktifkan via `settings.rate_limit_enabled=False` atau mengatur
ambang kecil untuk memicu 429. Test Redis memakai client tiruan (tanpa
Redis asli) — lihat tests/test_ratelimit.py.
"""

import logging
import threading
import time
from collections.abc import Callable

from fastapi import HTTPException, Request, status

from app.core.config import settings

_logger = logging.getLogger(__name__)


_limiter_lock = threading.Lock()
# (key, scope) -> (window_start, count) — window = epoch_detik // window_seconds
_windows: dict[tuple[str, str], tuple[int, int]] = {}
# Pruning berkala memory backend (window basi) — mencegah dict membesar
# tanpa batas di proses yang berjalan lama (trafik unik tiap window).
_prune_calls = 0
_PRUNE_EVERY = 128

# Redis client lazy (dibuat sekali per proses) + lock double-checked.
_redis_client = None
_redis_lock = threading.Lock()
# Prefix key rate limit — dipakai reset() untuk scan & hapus hanya milik kita.
_REDIS_PREFIX = "jernihai:ratelimit:"


def _use_redis() -> bool:
    return settings.rate_limit_backend == "redis"


def _get_redis_client():
    global _redis_client
    if _redis_client is None:
        with _redis_lock:
            if _redis_client is None:
                import redis.asyncio as aioredis

                _redis_client = aioredis.from_url(settings.redis_url)
    return _redis_client


def _window_start(now: int, window_seconds: int) -> int:
    return now // window_seconds


async def check(key: str, scope: str, limit: int, window_seconds: int = 60) -> None:
    """Tolak (429) bila key melewati `limit` dalam jendela waktu."""
    if _use_redis():
        await _check_redis(key, scope, limit, window_seconds)
    else:
        _check_memory(key, scope, limit, window_seconds)


def _check_memory(key: str, scope: str, limit: int, window_seconds: int) -> None:
    global _prune_calls
    now = int(time.time())
    window = _window_start(now, window_seconds)
    with _limiter_lock:
        # Buang window basi secara berkala — tanpanya dict tumbuh tanpa
        # batas (entri per scope:key per window tidak pernah dihapus).
        _prune_calls += 1
        if _prune_calls >= _PRUNE_EVERY:
            _prune_calls = 0
            _prune_windows(window)
        bucket = (key, scope)
        start, count = _windows.get(bucket, (window, 0))
        if start != window:
            start, count = window, 0
        if count >= limit:
            raise _too_many()
        _windows[bucket] = (start, count + 1)


def _prune_windows(current_window: int) -> None:
    """Hapus bucket dari window lebih lama dari satu window terakhir.

    Key rate limit memuat nomor window dan tidak pernah di-`get` lagi
    setelah windownya lewat — entri basi aman dibuang. Memori backend
    memory terbatas ~2 window trafik, bukan bertambah tanpa batas.
    """
    stale = [b for b, (start, _) in _windows.items() if start < current_window - 1]
    for b in stale:
        del _windows[b]


async def _check_redis(key: str, scope: str, limit: int, window_seconds: int) -> None:
    """Fixed-window di Redis: INCR + EXPIRE atomic (pipeline).

    Key = `jernihai:ratelimit:<scope>:<key>:<window>` — window berubah tiap
    menit sehingga counter lama terpisah; EXPIRE mencegah key menumpuk.
    """
    now = int(time.time())
    window = _window_start(now, window_seconds)
    rkey = f"{_REDIS_PREFIX}{scope}:{key}:{window}"
    client = _get_redis_client()
    try:
        pipe = client.pipeline()
        pipe.incr(rkey)
        # TTL = 2 jendela: key lama aman dibersihkan Redis, tapi window
        # berjalan tidak dihapus di tengah.
        pipe.expire(rkey, window_seconds * 2)
        count = (await pipe.execute())[0]
    except Exception as exc:  # fail-open: outage Redis ≠ endpoint ikut down
        _logger.warning(
            "Rate limit Redis tidak tersedia, dilewati (fail-open): %s", exc
        )
        return
    if count > limit:
        raise _too_many()


def _too_many() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Terlalu banyak permintaan — coba lagi beberapa saat lagi.",
    )


async def reset() -> None:
    """Kosongkan semua counter (dipakai test / reset manual).

    Redis: scan & hapus key ber-prefix `_REDIS_PREFIX` (hanya milik rate
    limit — jangan flush seluruh DB yang juga dipakai broker Celery).
    """
    if _use_redis():
        client = _get_redis_client()
        async for rkey in client.scan_iter(match=f"{_REDIS_PREFIX}*"):
            await client.delete(rkey)
        return
    with _limiter_lock:
        _windows.clear()


def rate_limit_dependency(
    scope: str,
    limit: int | Callable[[], int],
    window_seconds: int = 60,
):
    """Buat FastAPI dependency rate limit.

    `limit` boleh callable (dibaca per request) agar test bisa mengubah
    ambang via `monkeypatch.setattr(settings, ...)` tanpa restart.
    Bila `settings.rate_limit_enabled` False (umumnya test), dependency
    lulus tanpa cek — suite test tidak saling memicu 429.
    """

    async def dependency(request: Request) -> None:
        if not settings.rate_limit_enabled:
            return
        effective = limit() if callable(limit) else limit
        key = request.client.host if request.client else "unknown"
        await check(key, scope, effective, window_seconds)

    return dependency
