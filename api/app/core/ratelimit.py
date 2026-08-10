"""Rate limiting sederhana in-memory (NFR-04).

Fixed-window counter per (key, scope). Tanpa dependency eksternal —
cukup untuk MVP single-instance; multi-instance perlu storage bersama
(Redis) di Fase 3.

Dipakai sebagai FastAPI dependency pada endpoint sensitif (auth, upload).
Limiter global (modul) sehingga state bertahan selama proses; test
menonaktifkan lewat `settings.rate_limit_enabled=False` atau mengatur
ambang kecil untuk memicu 429.
"""

import threading
import time
from collections.abc import Callable

from fastapi import HTTPException, Request, status

from app.core.config import settings

_limiter_lock = threading.Lock()
# (key, scope) -> (window_start, count) — window = epoch_detik // window_seconds
_windows: dict[tuple[str, str], tuple[int, int]] = {}


def _window_start(now: int, window_seconds: int) -> int:
    return now // window_seconds


def check(key: str, scope: str, limit: int, window_seconds: int = 60) -> None:
    """Tolak (429) bila key melewati `limit` dalam jendela waktu."""
    now = int(time.time())
    window = _window_start(now, window_seconds)
    with _limiter_lock:
        bucket = (key, scope)
        start, count = _windows.get(bucket, (window, 0))
        if start != window:
            start, count = window, 0
        if count >= limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Terlalu banyak permintaan — coba lagi beberapa saat lagi.",
            )
        _windows[bucket] = (start, count + 1)


def reset() -> None:
    """Kosongkan semua counter (dipakai test / reset manual)."""
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
        check(key, scope, effective, window_seconds)

    return dependency
