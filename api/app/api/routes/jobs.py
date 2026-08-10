"""Endpoint jobs — FR-02 upload, FR-03 status (polling), FR-05 download,
FR-10 riwayat (list)."""

import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.quota import consume_quota, quota_remaining
from app.core.storage import detect_image_format, resolve, save_upload
from app.db.session import get_db
from app.models.job import Job, JobStatus
from app.models.user import User
from app.schemas.job import JobListOut, JobOut
from app.tasks.enhance import process_enhancement, process_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])

ALLOWED_SCALES = {2, 4}
ALLOWED_FORMATS = {"webp", "jpeg", "png"}
CONTENT_TYPES = {"webp": "image/webp", "jpeg": "image/jpeg", "png": "image/png"}


@router.post(
    "",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload gambar & mulai proses (FR-02/FR-03)",
)
async def create_job(
    file: UploadFile = File(...),
    scale: int = Form(2),
    output_format: str = Form("webp"),
    face_enhance: bool = Form(False),
    denoise: bool = Form(False),
    color_enhance: bool = Form(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Job:
    if scale not in ALLOWED_SCALES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"scale harus salah satu dari {sorted(ALLOWED_SCALES)}",
        )
    if output_format not in ALLOWED_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"output_format harus salah satu dari {sorted(ALLOWED_FORMATS)}",
        )

    # FR-06: cek kuota gratis SEBELUM membaca file — user yang sudah habis
    # jatah tidak perlu mengunggah (hemat bandwidth & disk).
    if quota_remaining(current_user) <= 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Kuota gratis harian sudah habis ({settings.free_daily_quota} "
                "gambar/hari). Kuota reset otomatis besok (00:00 WIB)."
            ),
        )

    data = await file.read()
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="File kosong"
        )
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Ukuran maksimal {settings.max_upload_bytes // (1024 * 1024)} MB",
        )
    ext = detect_image_format(data)
    if ext is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Hanya menerima JPG, PNG, atau WebP (validasi konten, bukan ekstensi)",
        )

    job_id = str(uuid4())
    original_path = save_upload(data=data, job_id=job_id, ext=ext)
    job = Job(
        id=job_id,
        user_id=current_user.id,
        scale=scale,
        output_format=output_format,
        face_enhance=face_enhance,
        denoise=denoise,
        color_enhance=color_enhance,
        original_name=file.filename or "gambar",
        original_path=original_path,
    )
    db.add(job)
    # FR-06: konsumsi 1 kuota saat job diterima (di-refund bila job gagal
    # di app/tasks/enhance.py). Digabung dalam satu transaksi dengan insert
    # job — commit di bawah.
    consume_quota(current_user)
    try:
        await db.commit()
    except Exception:
        # Hindari file yatim di disk bila insert DB gagal (P5 review).
        resolve(original_path).unlink(missing_ok=True)
        raise
    await db.refresh(job)

    if settings.celery_task_always_eager:
        # Dev/test tanpa Redis: proses inline agar alur end-to-end bisa diverifikasi.
        await process_job(job.id)
        await db.refresh(job)
    else:
        try:
            process_enhancement.delay(job.id)
        except Exception:
            # Broker mati: job tetap tersimpan berstatus queued; retry manual/admin.
            logger.warning("Gagal mengantre job %s ke broker Redis", job.id, exc_info=True)

    return job


async def _get_owned_job(
    db: AsyncSession, job_id: str, user: User
) -> Job:
    """Ambil job milik user; 404 untuk job milik orang lain (tanpa bocorkan info)."""
    result = await db.execute(
        select(Job).where(Job.id == job_id, Job.user_id == user.id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job tidak ditemukan"
        )
    return job


@router.get(
    "",
    response_model=JobListOut,
    summary="Riwayat job user (FR-10)",
)
async def list_jobs(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JobListOut:
    """Daftar job milik user, terbaru dulu (FR-10).

    Hanya job milik user yang login — tidak membocorkan job orang lain.
    Pagination `limit`/`offset`; `result_deleted_at` memberitahu UI bahwa
    hasil sudah dihapus retensi (tombol unduh ulang dinonaktifkan).
    """
    where = Job.user_id == current_user.id
    total = await db.scalar(select(func.count()).select_from(Job).where(where))
    # Sort stabil: created_at DESC + id DESC sebagai tiebreaker agar
    # pagination deterministik (tidak ada lompat/duplikat saat timestamp sama).
    result = await db.execute(
        select(Job)
        .where(where)
        .order_by(Job.created_at.desc(), Job.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return JobListOut(items=list(result.scalars()), total=total or 0)


@router.get(
    "/{job_id}",
    response_model=JobOut,
    summary="Cek status job (polling — FR-03)",
)
async def get_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Job:
    return await _get_owned_job(db, job_id, current_user)


@router.get(
    "/{job_id}/download",
    summary="Unduh hasil proses (FR-05)",
)
async def download_result(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FileResponse:
    job = await _get_owned_job(db, job_id, current_user)
    if job.result_deleted_at is not None:
        # FR-07: hasil sudah dihapus oleh retensi (7 hari) — jawab 410 Gone.
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Hasil sudah dihapus oleh retensi otomatis (disimpan 7 hari).",
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
            detail="File hasil tidak ditemukan (mungkin sudah terhapus oleh retensi)",
        )
    filename = f"{job.id}-{job.scale}x.{job.output_format}"
    return FileResponse(
        path,
        media_type=CONTENT_TYPES[job.output_format],
        filename=filename,
    )
