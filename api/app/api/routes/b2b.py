"""API Publik (FR-14 / B2B) — untuk developer.

Developer memanggil REST API dengan header `X-API-Key` (dibuat dari
halaman web `/api-keys`). Setiap job memakai **1 kredit** dari saldo
pemilik key (pay-per-call); tanpa saldo -> 402. Rate limit per menit
berdasarkan tier key (free/pro — NFR-04).

Manajemen key (buat/lihat/cabut) memakai sesi login user pemilik
(cookie), bukan API key.

Dokumentasi lengkap untuk developer: `docs/API_B2B.md`; spesifikasi
OpenAPI ekspor: `docs/api/openapi.yaml` (regenerate via
`python scripts/export_openapi.py`).
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
    _validate_png_output,
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

# --- Skema OpenAPI ---


class ErrorResponse(BaseModel):
    """Bentuk tubuh respons error — FastAPI HTTPException selalu `{"detail": ...}`."""

    detail: str


class ValidationErrorItem(BaseModel):
    """Satu item error validasi (bentuk 422 standar FastAPI)."""

    loc: list[str | int]
    msg: str
    type: str


class HTTPValidationError(BaseModel):
    """Bentuk 422 yang di-generate FastAPI untuk validasi request
    (detail berisi LIST error — berbeda dari ErrorResponse yang string)."""

    detail: list[ValidationErrorItem]


class B2bQuotaOut(BaseModel):
    """Sisa kredit + info tier untuk developer (FR-14)."""

    credit_balance: int = Field(
        ..., description="Sisa kredit pemilik key (1 job = 1 kredit)."
    )
    tier: str = Field(..., description="Tier key: `free` atau `pro` (menentukan rate limit).")
    rate_limit_per_minute: int = Field(
        ..., description="Batas permintaan per menit untuk tier ini."
    )


class ApiKeyOut(BaseModel):
    """Key yang ditampilkan di halaman /api-keys (TANPA key asli)."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="ID key (dipakai untuk mencabut key).")
    name: str = Field(..., description="Label bebas dari developer.")
    key_prefix: str = Field(
        ..., description="Awalan pendek key untuk tampilan, mis. `jn_AbC123xYz`."
    )
    tier: str = Field(
        ..., description="Tier key: `free` (20 req/menit) atau `pro` (120 req/menit)."
    )
    is_active: bool = Field(..., description="`false` setelah key dicabut.")
    created_at: datetime | None = Field(None, description="Waktu key dibuat.")
    last_used_at: datetime | None = Field(None, description="Terakhir kali key dipakai (audit).")
    revoked_at: datetime | None = Field(None, description="Waktu pencabutan key (None = aktif).")


class ApiKeyCreated(BaseModel):
    """Respons pembuatan key — memuat key asli SEKALI SAJA."""

    key: ApiKeyOut = Field(..., description="Metadata key (tanpa key asli).")
    full_key: str = Field(
        ...,
        description=(
            "Key asli (`jn_...`) — TAMPILKAN SEKALI dan simpan; "
            "tidak akan pernah muncul lagi."
        ),
    )


class ApiKeyCreateRequest(BaseModel):
    """Buat key baru; `tier` menentukan rate limit per menit."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"name": "Produksi", "tier": "pro"}],
        }
    )

    # max_length selaras kolom String(100) — tanpa ini nama >100 karakter
    # akan error DB (500) di Postgres, bukan 422 dari validasi skema.
    name: str = Field(
        min_length=1, max_length=100,
        description="Label bebas, mis. `Produksi` / `Staging`.",
    )
    tier: str = Field(
        "free",
        description="Tier key: `free` (20 req/menit) atau `pro` (120 req/menit).",
    )


class ApiKeyList(BaseModel):
    items: list[ApiKeyOut]
    total: int


def _err(code: int, desc: str, example: str) -> dict:
    """Entri OpenAPI untuk satu kode error: skema `ErrorResponse` + contoh detail."""
    return {
        code: {
            "model": ErrorResponse,
            "description": desc,
            "content": {"application/json": {"example": {"detail": example}}},
        }
    }


def _err_validation(desc: str, example_msg: str) -> dict:
    """Entri 422 untuk validasi FastAPI: `detail` adalah LIST (bukan string)."""
    return {
        422: {
            "model": HTTPValidationError,
            "description": desc,
            "content": {
                "application/json": {
                    "example": {
                        "detail": [
                            {
                                "loc": ["body", "name"],
                                "msg": example_msg,
                                "type": "value_error",
                            }
                        ]
                    }
                }
            },
        }
    }


# Error yang berlaku di SEMUA endpoint job/kuota B2B (auth via X-API-Key).
_AUTH_ERRORS = {
    **_err(
        401,
        "Header `X-API-Key` tidak ada atau key tidak dikenal",
        "Header X-API-Key wajib diisi (lihat /api-keys)",
    ),
    **_err(
        403,
        "Key dinonaktifkan (dicabut) atau akun pemilik di-suspend",
        "API key dinonaktifkan (dicabut oleh pemilik)",
    ),
    **_err(
        429,
        "Rate limit per menit tier terlampaui (free 20 / pro 120)",
        "Terlalu banyak permintaan — coba lagi beberapa saat lagi.",
    ),
}


async def _get_owned_key(db: AsyncSession, key_id: str, user: User) -> ApiKey:
    key = await db.scalar(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user.id)
    )
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="API key tidak ditemukan"
        )
    return key


# --- Endpoint job & kuota (dipanggil dengan X-API-Key) ---


@router.post(
    "/jobs",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
    summary="B2B: upload gambar & mulai proses (FR-14)",
    response_description="Job dibuat (status awal `queued`) dan 1 kredit terpotong dari saldo.",
    responses={
        **_err(
            400,
            "`scale`/`output_format` tidak dikenal, atau PNG melebihi batas "
            "4096 px sisi terpanjang (ADR-004)",
            "scale harus salah satu dari [2, 4]",
        ),
        **_err(
            402,
            "Saldo kredit kosong — isi lewat halaman Billing web",
            "Saldo kredit kosong (B2B: 1 gambar = 1 kredit). Isi saldo via halaman Billing web.",
        ),
        **_err(
            413,
            "File melebihi batas ukuran (10 MB)",
            "Ukuran maksimal 10 MB",
        ),
        **_err(
            415,
            "Format file tidak didukung — hanya JPG/PNG/WebP (dicek konten)",
            "Hanya menerima JPG, PNG, atau WebP (validasi konten, bukan ekstensi)",
        ),
        **_err_validation(
            "Validasi form gagal (mis. `scale` bukan int, `file` kosong) — "
            "`detail` berupa LIST error",
            "Field required",
        ),
        **_AUTH_ERRORS,
    },
)
async def b2b_create_job(
    file: UploadFile = File(
        ...,
        description=(
            "Gambar input: JPG/PNG/WebP, maks 10 MB "
            "(validasi magic bytes, bukan ekstensi)."
        ),
    ),
    scale: int = Form(
        2,
        description="Faktor pembesaran (2x atau 4x).",
        examples=[2, 4],
    ),
    output_format: str = Form(
        "webp",
        description=(
            "Format hasil: `webp` (default, terbaik), `jpeg`, atau `png` "
            "(lossless — dibatasi ≤ 4096 px sisi terpanjang, ADR-004)."
        ),
        examples=["webp", "jpeg", "png"],
    ),
    face_enhance: bool = Form(
        False,
        description="Restorasi wajah (GFPGAN) — memperjelas wajah pada foto lama.",
    ),
    denoise: bool = Form(
        False,
        description="Kurangi noise/grain pada foto lama.",
    ),
    color_enhance: bool = Form(
        False,
        description="Pertegas warna (saturasi/kontras) untuk foto pudar.",
    ),
    db: AsyncSession = Depends(get_db),
    ctx: ApiKeyContext = Depends(api_key_rate_limit()),
) -> Job:
    """Pay-per-call: 1 job = 1 kredit dari saldo pemilik key.

    Potongan kredit bersifat **atomik** (2 request bersamaan tidak bisa
    memakai saldo yang sama). Tanpa saldo -> 402 Payment Required.
    Status awal `queued` — job diproses worker asinkron; polling dengan
    `GET /b2b/jobs/{id}` hingga `completed`.
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
    # ADR-004: PNG lossless dibatasi 4096 px — cek SEBELUM kredit dipotong
    # (user tidak membayar untuk job yang pasti gagal).
    _validate_png_output(data, output_format)
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
    response_description="Status job terkini (`queued`/`processing`/`completed`/`failed`).",
    responses={
        **_err(
            404,
            "Job tidak ditemukan (atau milik key lain — tanpa bocor info)",
            "Job tidak ditemukan",
        ),
        **_AUTH_ERRORS,
    },
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
    response_description=(
        "File hasil (binary). Media type mengikuti `output_format` job: "
        "`image/webp` (default), `image/jpeg`, atau `image/png`."
    ),
    responses={
        200: {
            "description": "File hasil proses.",
            "content": {
                media: {"schema": {"type": "string", "format": "binary"}}
                for media in ("image/webp", "image/jpeg", "image/png")
            },
        },
        **_err(404, "Job tidak ditemukan / file hasil tidak ada", "Job tidak ditemukan"),
        **_err(
            409,
            "Hasil belum siap — job belum `completed`",
            "Hasil belum siap (status: processing)",
        ),
        **_err(
            410,
            "Hasil sudah dihapus retensi otomatis (7 hari free)",
            "Hasil sudah dihapus oleh retensi otomatis.",
        ),
        **_AUTH_ERRORS,
    },
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
    response_description="Sisa kredit dan batas rate limit tier key.",
    responses={**_AUTH_ERRORS},
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
    response_description="Daftar key milik user yang login (tanpa key asli).",
    responses={
        **_err(401, "Belum login (butuh sesi cookie web)", "Belum login"),
    },
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
    response_description=(
        "Key baru dibuat — `full_key` tampil SEKALI (hanya hash SHA-256 tersimpan)."
    ),
    responses={
        **_err(
            400,
            "Tier tidak dikenal (harus `free` atau `pro`)",
            "tier harus salah satu dari ['free', 'pro']",
        ),
        **_err_validation(
            "Validasi nama gagal — `detail` berupa LIST error; nama yang "
            "hanya spasi menjawab 422 dengan `detail` string "
            "'Nama key wajib diisi'",
            "String should have at least 1 character",
        ),
        **_err(401, "Belum login (butuh sesi cookie web)", "Belum login"),
    },
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
    response_description="Key dinonaktifkan — semua permintaan dengannya ditolak 403.",
    responses={
        **_err(404, "Key tidak ditemukan (atau milik user lain)", "API key tidak ditemukan"),
        **_err(401, "Belum login (butuh sesi cookie web)", "Belum login"),
    },
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
