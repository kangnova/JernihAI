"""API Publik (FR-14 / B2B) — untuk developer.

Developer memanggil REST API dengan header `X-API-Key` (dibuat dari
halaman web `/api-keys`). Setiap job memakai **1 kredit** dari saldo
pemilik key (pay-per-call); tanpa saldo -> 402. Rate limit per menit
berdasarkan tier key (free/pro — NFR-04).

Manajemen key (buat/lihat/cabut) memakai sesi login user pemilik
(cookie), bukan API key.
"""

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ApiKeyContext, api_key_rate_limit, get_current_user
from app.api.routes.jobs import (
    CONTENT_TYPES,
    _build_job,
    _enqueue,
    _read_and_validate,
    _validate_options,
)
from app.core.apikey import generate_api_key
from app.core.config import settings
from app.core.storage import resolve, save_upload
from app.db.session import get_db
from app.models.apikey import ApiKey
from app.models.job import Job, JobStatus
from app.models.user import User
from app.schemas.job import JobOut

router = APIRouter(prefix="/b2b", tags=["b2b"])

ALLOWED_TIERS = {"free", "pro"}


class B2bQuotaOut(BaseModel):
    """Sisa kredit + info tier untuk developer (FR-14)."""

    credit_balance: int
    tier: str
    rate_limit_per_minute: int


class ApiKeyOut(BaseModel):
    """Key yang ditampilkan di halaman /api-keys (TANPA key asli)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    key_prefix: str
    tier: str
    is_active: bool
    created_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreated(BaseModel):
    """Respons pembuatan key — memuat key asli SEKALI SAJA."""

    key: ApiKeyOut
    full_key: str  # tampilkan sekali; tidak akan pernah muncul lagi


class ApiKeyCreateRequest(BaseModel):
    # max_length selaras kolom String(100) — tanpa ini nama >100 karakter
    # akan error DB (500) di Postgres, bukan 422 dari validasi skema.
    name: str = Field(min_length=1, max_length=100)
    tier: str = "free"


class ApiKeyList(BaseModel):
    items: list[ApiKeyOut]
    total: int


async def _get_owned_key(db: AsyncSession, key_id: str, user: User) -> ApiKey:
    key = await db.scalar(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user.id)
    )
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="API key tidak ditemukan"
        )
    return key


@router.post(
    "/jobs",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
    summary="B2B: upload gambar & mulai proses (FR-14)",
)
async def b2b_create_job(
    file: UploadFile = File(...),
    scale: int = Form(2),
    output_format: str = Form("webp"),
    face_enhance: bool = Form(False),
    denoise: bool = Form(False),
    color_enhance: bool = Form(False),
    db: AsyncSession = Depends(get_db),
    ctx: ApiKeyContext = Depends(api_key_rate_limit()),
) -> Job:
    """Pay-per-call: 1 job = 1 kredit dari saldo pemilik key.

    Tanpa saldo -> 402 Payment Required (detail berisi alur top-up).
    """
    _validate_options(scale, output_format)
    user = ctx.user

    # Fast-path 402 SEBELUM membaca file (hemat bandwidth & disk — pola
    # jobs.py). Potongan otoritatif tetap di UPDATE atomik di bawah.
    if (user.credit_balance or 0) < 1:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                "Saldo kredit kosong (B2B: 1 gambar = 1 kredit). "
                "Isi saldo via halaman Billing web."
            ),
        )

    data, ext = await _read_and_validate(file)
    job_id = str(uuid4())
    original_path = save_upload(data=data, job_id=job_id, ext=ext)
    job = _build_job(
        job_id, user, original_path, file.filename or "gambar",
        scale, output_format, face_enhance, denoise, color_enhance,
    )
    job.uses_credit = True  # B2B selalu bayar kredit (bukan kuota gratis)

    # Potongan kredit ATOMIK: `UPDATE ... WHERE credit_balance >= 1` — dua
    # request B2B bersamaan tidak bisa sama-sama lolos cek & memotong saldo
    # yang sama (double-spend). rowcount 0 = saldo habis (kompetisi kalah).
    result = await db.execute(
        update(User)
        .where(User.id == user.id, User.credit_balance >= 1)
        .values(credit_balance=User.credit_balance - 1)
    )
    if result.rowcount == 0:
        resolve(original_path).unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                "Saldo kredit kosong (B2B: 1 gambar = 1 kredit). "
                "Isi saldo via halaman Billing web."
            ),
        )

    db.add(job)
    try:
        await db.commit()
    except Exception:
        resolve(original_path).unlink(missing_ok=True)
        raise
    await db.refresh(job)

    # Broker Redis down: job tetap tersimpan berstatus queued, kredit sudah
    # terpotong — stale-check (30 mnt -> failed -> refund) jadi jaring
    # pengaman akhir (sama dengan alur jobs.py).
    await _enqueue(job, db)
    return job


@router.get(
    "/jobs/{job_id}",
    response_model=JobOut,
    summary="B2B: cek status job (polling — FR-14)",
)
async def b2b_get_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    ctx: ApiKeyContext = Depends(api_key_rate_limit()),
) -> Job:
    """Status job milik pemilik key — job key lain = 404 (tanpa bocor info)."""
    job = await db.scalar(
        select(Job).where(Job.id == job_id, Job.user_id == ctx.user.id)
    )
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job tidak ditemukan"
        )
    return job


@router.get(
    "/jobs/{job_id}/result",
    summary="B2B: unduh hasil proses (FR-14)",
)
async def b2b_download_result(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    ctx: ApiKeyContext = Depends(api_key_rate_limit()),
) -> FileResponse:
    job = await db.scalar(
        select(Job).where(Job.id == job_id, Job.user_id == ctx.user.id)
    )
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job tidak ditemukan"
        )
    if job.result_deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Hasil sudah dihapus oleh retensi otomatis.",
        )
    if job.status != JobStatus.COMPLETED.value or not job.result_path:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Hasil belum siap (status: " + (job.status or "?") + ")",
        )
    path = resolve(job.result_path)
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File hasil tidak ditemukan",
        )
    return FileResponse(
        path,
        media_type=CONTENT_TYPES[job.output_format],
        filename=f"{job.id}-{job.scale}x.{job.output_format}",
    )


@router.get(
    "/quota",
    response_model=B2bQuotaOut,
    summary="B2B: sisa kredit + rate limit tier",
)
async def b2b_quota(
    ctx: ApiKeyContext = Depends(api_key_rate_limit()),
) -> B2bQuotaOut:
    limit = (
        settings.api_rate_limit_pro_per_minute
        if ctx.key.tier == "pro"
        else settings.api_rate_limit_free_per_minute
    )
    return B2bQuotaOut(
        credit_balance=ctx.user.credit_balance or 0,
        tier=ctx.key.tier,
        rate_limit_per_minute=limit,
    )


# --- Manajemen key (sesi login user pemilik) ---


@router.get(
    "/keys",
    response_model=ApiKeyList,
    summary="Daftar API key saya (FR-14)",
)
async def list_api_keys(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiKeyList:
    total = (
        await db.scalar(
            select(func.count()).select_from(ApiKey).where(
                ApiKey.user_id == current_user.id
            )
        )
        or 0
    )
    keys = (
        await db.execute(
            select(ApiKey)
            .where(ApiKey.user_id == current_user.id)
            .order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
        )
    ).scalars()
    return ApiKeyList(items=list(keys), total=total)


@router.post(
    "/keys",
    response_model=ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Buat API key baru (FR-14)",
)
async def create_api_key(
    body: ApiKeyCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiKeyCreated:
    name = body.name.strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Nama key wajib diisi",
        )
    if body.tier not in ALLOWED_TIERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"tier harus salah satu dari {sorted(ALLOWED_TIERS)}",
        )

    full_key, key_hash, key_prefix = generate_api_key()
    key = ApiKey(
        id=str(uuid4()),
        user_id=current_user.id,
        name=name,
        key_hash=key_hash,
        key_prefix=key_prefix,
        tier=body.tier,
    )
    db.add(key)
    await db.commit()
    await db.refresh(key)
    return ApiKeyCreated(key=key, full_key=full_key)


@router.delete(
    "/keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cabut (revoke) API key (FR-14)",
)
async def revoke_api_key(
    key_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    key = await _get_owned_key(db, key_id, current_user)
    key.is_active = False
    key.revoked_at = datetime.now(UTC)
    await db.commit()
