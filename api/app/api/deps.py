"""Shared FastAPI dependencies (auth, db session)."""

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.apikey import hash_api_key
from app.core.config import settings
from app.core.ratelimit import check as rate_limit_check
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.apikey import ApiKey
from app.models.user import User


async def get_current_user(
    request: Request, db: AsyncSession = Depends(get_db)
) -> User:
    token = request.cookies.get(settings.cookie_name)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Belum login",
        )
    user_id = decode_access_token(token)
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Akun tidak ditemukan atau dinonaktifkan",
        )
    return user


async def require_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """FR-13: hanya admin (email di ADMIN_EMAILS) yang boleh lewat."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akses khusus admin",
        )
    return current_user


@dataclass
class ApiKeyContext:
    """Konteks autentikasi API publik (FR-14): key + pemilik."""
    key: ApiKey
    user: User


async def get_api_key(
    request: Request, db: AsyncSession = Depends(get_db)
) -> ApiKeyContext:
    """Autentikasi API publik via header `X-API-Key` (FR-14).

    Key dicari berdasarkan hash SHA-256; key yang dicabut (nonaktif)
    atau pemilik yang di-suspend ditolak. `last_used_at` diperbarui
    (audit).
    """
    raw = request.headers.get("X-API-Key")
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Header X-API-Key wajib diisi (lihat /api-keys)",
        )
    key = await db.scalar(
        select(ApiKey).where(ApiKey.key_hash == hash_api_key(raw.strip()))
    )
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key tidak valid",
        )
    if not key.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API key dinonaktifkan (dicabut oleh pemilik)",
        )
    user = await db.get(User, key.user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akun pemilik API key tidak aktif",
        )
    # Audit `last_used_at` ditulis paling banyak sekali per 60 detik per
    # key — menghindari 1 commit DB per request (NFR-02 skalabilitas).
    now = datetime.now(UTC)
    last = key.last_used_at
    if last is None:
        key.last_used_at = now
        await db.commit()
    else:
        # SQLite mengembalikan datetime NAIVE (timezone tak tersimpan)
        # meski kolom bertimezone — bandingkan dengan now yang senada.
        if last.tzinfo is None:
            stale = (datetime.now() - last).total_seconds() > 60
        else:
            stale = (now - last).total_seconds() > 60
        if stale:
            key.last_used_at = now
            await db.commit()
    return ApiKeyContext(key=key, user=user)


def api_key_rate_limit():
    """Dependency rate limit per tier (FR-14, NFR-04).

    `limit` dibaca per request dari settings agar test bisa mengubah
    ambang via monkeypatch tanpa restart. Key dihitung per key id.
    """

    async def dependency(
        ctx: ApiKeyContext = Depends(get_api_key),
    ) -> ApiKeyContext:
        if not settings.rate_limit_enabled:
            return ctx
        limit = (
            settings.api_rate_limit_pro_per_minute
            if ctx.key.tier == "pro"
            else settings.api_rate_limit_free_per_minute
        )
        rate_limit_check(f"key:{ctx.key.id}", "api:key", limit)
        return ctx

    return dependency
