"""Endpoint auth — FR-01 (email+password & Google OAuth).

Alur token: JWT ditaruh di httpOnly cookie (ADR-003). Login sukses
mengatur cookie di response; logout menghapusnya.
"""

import secrets
from datetime import UTC, datetime
from urllib.parse import urlencode
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.ratelimit import rate_limit_dependency
from app.core.security import (
    clear_auth_cookie,
    create_access_token,
    hash_password,
    set_auth_cookie,
    verify_password,
)
from app.db.session import get_db
from app.models.user import AuthProvider, User
from app.schemas.auth import LoginRequest, RegisterRequest, UserOut

router = APIRouter(tags=["auth"])

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


def _auth_rate_limit(scope: str):
    """Dependency rate limit auth (NFR-04) — ambang dari settings per-request."""
    return rate_limit_dependency(scope, lambda: settings.rate_limit_auth_per_minute)


async def _get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email.lower()))
    return result.scalar_one_or_none()


def _google_callback_uri() -> str:
    """redirect_uri OAuth — sumber tunggal kebenaran untuk kedua langkah
    (authorize & token exchange). `WEB_URL` dibersihkan dari trailing slash
    agar tidak menghasilkan `//api/...` (penyebab redirect_uri_mismatch yang
    sulit dilacak)."""
    return f"{settings.web_url.rstrip('/')}/api/v1/auth/google/callback"


@router.post(
    "/auth/register",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Daftar akun baru",
)
async def register(
    body: RegisterRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_auth_rate_limit("auth:register")),
) -> User:
    # FR-07 (UU PDP): consent eksplisit wajib — tolak bila tidak disetujui.
    if not body.privacy_consent:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Kamu harus menyetujui kebijakan privasi untuk mendaftar "
                "(gambar asli dihapus otomatis setelah 24 jam, hasil setelah 7 hari)"
            ),
        )
    if await _get_user_by_email(db, body.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email sudah terdaftar",
        )

    user = User(
        id=str(uuid4()),
        email=body.email.lower(),
        name=body.name.strip(),
        password_hash=hash_password(body.password),
        provider=AuthProvider.LOCAL.value,
        privacy_consent_at=datetime.now(UTC),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    set_auth_cookie(response, create_access_token(user.id))
    return user


@router.post(
    "/auth/login",
    response_model=UserOut,
    summary="Login email + password",
)
async def login(
    body: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_auth_rate_limit("auth:login")),
) -> User:
    user = await _get_user_by_email(db, body.email)
    if user is None or not user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email atau password salah",
        )
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email atau password salah",
        )
    if not user.is_active:
        # Akun dinonaktifkan oleh pengelola (alat admin) — tolak login.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akun dinonaktifkan. Hubungi pengelola.",
        )

    set_auth_cookie(response, create_access_token(user.id))
    return user


@router.post("/auth/logout", summary="Keluar (hapus cookie sesi)")
async def logout(response: Response) -> dict[str, str]:
    clear_auth_cookie(response)
    return {"status": "ok"}


@router.get("/auth/me", response_model=UserOut, summary="Profil user yang login")
async def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@router.post(
    "/auth/consent",
    response_model=UserOut,
    summary="Setujui kebijakan privasi (FR-07)",
)
async def grant_privacy_consent(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> User:
    """Catat persetujuan privasi — dipakai user Google OAuth yang daftar
    tanpa form (consent diminta lewat banner di dashboard). Idempoten.
    """
    if current_user.privacy_consent_at is None:
        current_user.privacy_consent_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(current_user)
    return current_user
@router.get(
    "/auth/google",
    summary="Mulai login Google (redirect ke Google)",
    status_code=status.HTTP_302_FOUND,
    responses={
        302: {"description": "Redirect ke Google OAuth consent"},
        503: {"description": "Google login belum dikonfigurasi (GOOGLE_CLIENT_ID kosong)"},
    },
)
async def google_login() -> RedirectResponse:
    if not settings.google_client_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google login belum dikonfigurasi (GOOGLE_CLIENT_ID kosong)",
        )
    # state (login CSRF): nilai acak dikirim ke Google & disimpan di cookie
    # sesi pendek; callback harus mengembalikannya persis (secrets.compare_digest).
    state = secrets.token_urlsafe(24)
    params = urlencode(
        {
            "client_id": settings.google_client_id,
            "redirect_uri": _google_callback_uri(),
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
        }
    )
    # Redirect langsung (bukan JSON) — tombol "Lanjut dengan Google" di web
    # adalah anchor biasa, jadi browser harus diarahkan ke Google di sini.
    response = RedirectResponse(
        url=f"{GOOGLE_AUTH_URL}?{params}",
        status_code=status.HTTP_302_FOUND,
    )
    response.set_cookie(
        "oauth_state",
        state,
        max_age=600,  # 10 menit — sesi login pendek
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/api/v1/auth/google/callback",
    )
    return response


@router.get(
    "/auth/google/callback",
    summary="Callback Google — tukar code dengan token lalu set cookie",
    response_model=None,
    responses={
        303: {"description": "Redirect ke web setelah login sukses"},
        400: {"description": "State OAuth tidak valid / email tidak tersedia"},
        401: {"description": "Gagal menukar kode Google"},
        403: {"description": "Akun dinonaktifkan"},
        503: {"description": "Google login belum dikonfigurasi"},
    },
)
async def google_callback(
    request: Request,
    code: str | None = None,
    error: str | None = None,
    state: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> Response:
    if not settings.google_client_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google login belum dikonfigurasi",
        )

    # User menolak consent / membatalkan di Google → redirect balik ke web
    # (bukan 422 JSON mentah). Gagal karena error lain juga dilimpahkan.
    if error or not code:
        return RedirectResponse(
            url=settings.web_url, status_code=status.HTTP_303_SEE_OTHER
        )

    # Login CSRF: state dari Google harus persis sama dengan cookie yang
    # kita set saat memulai login (bandingkan secara constant-time).
    expected_state = request.cookies.get("oauth_state")
    if not expected_state or not state or not secrets.compare_digest(
        expected_state, state
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Parameter state OAuth tidak valid — mulai login lagi",
        )

    # redirect_uri HARUS persis sama dengan yang dipakai saat authorize
    # (dan yang didaftarkan di Google Console). Dipakai web_url dari settings,
    # bukan request.url_for — yang terakhir bergantung pada Host/forwarded
    # header proxy dan bisa menghasilkan http:// padahal terdaftar https://
    # (redirect_uri_mismatch). web_url = sumber tunggal kebenaran.
    redirect_uri = _google_callback_uri()

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if token_resp.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Gagal menukar kode Google",
            )
        tokens = token_resp.json()
        userinfo_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        info = userinfo_resp.json()

    email = info.get("email")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Akun Google tidak memiliki email",
        )
    # Hardening: tolak akun Google dengan email belum terverifikasi
    # (email_verified=False). None dianggap lolos (userinfo lama/parsial).
    if info.get("email_verified") is False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email Google belum terverifikasi",
        )

    user = await _get_user_by_email(db, email)
    if user is None:
        user = User(
            id=str(uuid4()),
            email=email.lower(),
            name=info.get("name"),
            provider=AuthProvider.GOOGLE.value,
            provider_sub=info.get("sub"),
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    elif not user.is_active:
        # Akun dinonaktifkan oleh pengelola — tolak masuk Google OAuth.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akun dinonaktifkan. Hubungi pengelola.",
        )

    response = RedirectResponse(url=settings.web_url, status_code=status.HTTP_303_SEE_OTHER)
    set_auth_cookie(response, create_access_token(user.id))
    # State CSRF hanya dipakai sekali — hapus agar tidak bisa dipakai ulang.
    response.delete_cookie(
        "oauth_state",
        path="/api/v1/auth/google/callback",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response
