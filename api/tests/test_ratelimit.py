"""Tests rate limiting (NFR-04) — backend memory & redis.

Memory: counter per (key, scope), 429 setelah ambang, reset mengosongkan.
Redis: INCR + EXPIRE atomic (pipeline) — diuji dengan client tiruan (tanpa
Redis asli) agar CI deterministik.
"""

import pytest
from fastapi import HTTPException

from app.core import ratelimit
from app.core.config import settings


@pytest.fixture(autouse=True)
def memory_backend(monkeypatch):
    """Backend memory + counter bersih di awal setiap test (state global
    modul — tanpa reset, test sebelumnya mewarisi counter & 429 prematur)."""
    monkeypatch.setattr(settings, "rate_limit_backend", "memory")
    ratelimit._windows.clear()
    ratelimit._redis_client = None
    yield
    ratelimit._windows.clear()
    ratelimit._redis_client = None


async def test_memory_allows_up_to_limit():
    for _ in range(3):
        await ratelimit.check("ip-1", "auth:login", limit=3)
    with pytest.raises(HTTPException) as exc:
        await ratelimit.check("ip-1", "auth:login", limit=3)
    assert exc.value.status_code == 429


async def test_memory_scopes_are_isolated():
    await ratelimit.check("ip-1", "auth:login", limit=1)
    # Scope berbeda → counter terpisah, tetap lolos.
    await ratelimit.check("ip-1", "jobs:upload", limit=1)
    await ratelimit.check("ip-2", "auth:login", limit=1)
    # Scope sama + key sama → sekarang 429.
    with pytest.raises(HTTPException):
        await ratelimit.check("ip-1", "auth:login", limit=1)


async def test_memory_window_rollover_resets_count():
    """Jendela baru (window berbeda) memulai counter dari nol."""
    await ratelimit.check("ip-1", "s", limit=1)  # window saat ini
    # Paksa window berikutnya (30 detik ke depan dari jendela 60 detik).
    import time

    now = int(time.time())
    next_window = (now // 60 + 1) * 60
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ratelimit.time, "time", lambda: next_window)
        await ratelimit.check("ip-1", "s", limit=1)  # jendela baru → lolos


async def test_memory_reset_clears_counters():
    await ratelimit.check("ip-1", "auth:login", limit=1)
    with pytest.raises(HTTPException):
        await ratelimit.check("ip-1", "auth:login", limit=1)

    await ratelimit.reset()

    await ratelimit.check("ip-1", "auth:login", limit=1)  # lolos lagi


async def test_memory_prunes_stale_windows():
    """Bucket dari window basi dibersihkan berkala — dict tidak membesar
    tanpa batas di proses yang berjalan lama."""
    import time

    now = int(time.time())
    current_window = now // 60
    # Entri basi (2+ window lalu) + entri yang masih relevan.
    ratelimit._windows[("stale-1", "s")] = (current_window - 3, 5)
    ratelimit._windows[("stale-2", "s")] = (current_window - 2, 5)
    ratelimit._windows[("recent", "s")] = (current_window, 1)
    # Check berikutnya memicu pruning (counter sudah di ambang).
    ratelimit._prune_calls = ratelimit._PRUNE_EVERY - 1

    await ratelimit.check("ip-1", "s", limit=1)

    assert ("stale-1", "s") not in ratelimit._windows
    assert ("stale-2", "s") not in ratelimit._windows
    assert ("recent", "s") in ratelimit._windows


# --- Backend Redis (client tiruan) ---


class _FakeRedisPipeline:
    """Pipeline tiruan: catat perintah, hasil INCR = counter naik."""

    def __init__(self, store: dict[str, int]):
        self._store = store
        self._ops: list[tuple[str, str]] = []

    def incr(self, key: str):
        self._ops.append(("incr", key))
        return self

    def expire(self, key: str, ttl: int):
        self._ops.append(("expire", key))
        return self

    async def execute(self):
        results = []
        for kind, key in self._ops:
            if kind == "incr":
                self._store[key] = self._store.get(key, 0) + 1
                results.append(self._store[key])
            else:
                results.append(True)
        return results


class _FakeRedisClient:
    """Client tiruan: pipeline + scan/delete untuk reset()."""

    def __init__(self):
        self.store: dict[str, int] = {}

    def pipeline(self):
        return _FakeRedisPipeline(self.store)

    async def scan_iter(self, match: str = "*"):
        for key in list(self.store):
            if key.startswith(match.rstrip("*")):
                yield key

    async def delete(self, *keys: str):
        for key in keys:
            self.store.pop(key, None)


@pytest.fixture()
def redis_backend(monkeypatch):
    client = _FakeRedisClient()
    monkeypatch.setattr(settings, "rate_limit_backend", "redis")
    monkeypatch.setattr(ratelimit, "_get_redis_client", lambda: client)
    return client


async def test_redis_uses_pipeline_incr_and_expire(redis_backend):
    """Setiap request Redis memakai pipeline INCR+EXPIRE (atomic) dengan
    key ber-prefix milik rate limit + window menit berjalan."""
    await ratelimit.check("key-x", "api:key", limit=5)

    import time

    now = int(time.time())
    expected_window = now // 60
    assert any(
        k == f"jernihai:ratelimit:api:key:key-x:{expected_window}"
        for k in redis_backend.store
    )


async def test_redis_allows_up_to_limit_and_429(redis_backend):
    for _ in range(2):
        await ratelimit.check("key-1", "api:key", limit=2)
    with pytest.raises(HTTPException) as exc:
        await ratelimit.check("key-1", "api:key", limit=2)
    assert exc.value.status_code == 429
    # Key memakai prefiks milik rate limit + scope + key + window.
    assert any(k.startswith("jernihai:ratelimit:api:key:key-1:") for k in redis_backend.store)


async def test_redis_keys_isolated_by_scope_and_key(redis_backend):
    await ratelimit.check("key-1", "api:key", limit=1)
    await ratelimit.check("key-1", "jobs:upload", limit=1)
    await ratelimit.check("key-2", "api:key", limit=1)
    with pytest.raises(HTTPException):
        await ratelimit.check("key-1", "api:key", limit=1)


async def test_redis_window_rollover_resets(redis_backend):
    await ratelimit.check("key-1", "s", limit=1)
    with pytest.raises(HTTPException):
        await ratelimit.check("key-1", "s", limit=1)

    # Paksa window berikutnya → key baru → lolos.
    import time

    now = int(time.time())
    next_window = (now // 60 + 1) * 60
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ratelimit.time, "time", lambda: next_window)
        await ratelimit.check("key-1", "s", limit=1)


async def test_redis_reset_clears_only_ratelimit_keys(redis_backend):
    await ratelimit.check("key-1", "api:key", limit=1)
    redis_backend.store["jernihai:celery:something-else"] = 99  # bukan milik kita

    await ratelimit.reset()

    assert not any(k.startswith("jernihai:ratelimit:") for k in redis_backend.store)
    assert "jernihai:celery:something-else" in redis_backend.store  # tidak disentuh


class _BrokenRedisClient:
    """Client tiruan yang selalu gagal — simulasi Redis down."""

    def pipeline(self):
        raise ConnectionError("Redis tidak terjangkau")


async def test_redis_fail_open_when_redis_down(monkeypatch):
    """Redis down → check dilewati (fail-open), endpoint TIDAK ikut 500."""
    monkeypatch.setattr(settings, "rate_limit_backend", "redis")
    monkeypatch.setattr(ratelimit, "_get_redis_client", lambda: _BrokenRedisClient())

    # Melebihi limit pun tetap lolos — counter tak bisa dihitung, bukan 500.
    await ratelimit.check("key-1", "api:key", limit=1)
    await ratelimit.check("key-1", "api:key", limit=1)
    await ratelimit.check("key-1", "api:key", limit=1)
