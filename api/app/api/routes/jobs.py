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
from app.core.quota import consume_slots, slots_available
from app.core.ratelimit import rate_limit_dependency
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
# NFR-04: ambang upload per menit per IP (dibaca per request agar test
# bisa mengubah via settings tanpa restart).
_upload_rate_limit = rate_limit_dependency(
    "jobs:upload", lambda: settings.rate_limit_upload_per_minute
)


def _validate_options(scale: int, output_format: str) -> None:
    """Validasi opsi proses (scale/format) — raise 400 bila tidak dikenal."""
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


async def _read_and_validate(file: UploadFile) -> tuple[bytes, str]:
    """Baca file + validasi (kosong/ukuran/magic bytes); return (data, ext)."""
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
    return data, ext


def _build_job(
    job_id: str,
    current_user: User,
    original_path: str,
    original_name: str,
    scale: int,
    output_format: str,
    face_enhance: bool,
    denoise: bool,
    color_enhance: bool,
) -> Job:
    """Buat Job dengan `job_id` yang SAMA dengan nama file original
    (`save_upload` menamai file `<job_id>.<ext>` — jangan generate uuid
    baru di sini, nanti path & id tidak cocok).
    """
    return Job(
        id=job_id,
        user_id=current_user.id,
        scale=scale,
        output_format=output_format,
        face_enhance=face_enhance,
        denoise=denoise,
        color_enhance=color_enhance,
        original_name=original_name,
        original_path=original_path,
    )


async def _enqueue(job: Job, db: AsyncSession) -> None:
    """Antrekan job: inline (eager dev/test) atau ke broker Redis."""
    if settings.celery_task_always_eager:
        await process_job(job.id)
        await db.refresh(job)
    else:
        try:
            process_enhancement.delay(job.id)
        except Exception:
            # Broker mati: job tetap tersimpan berstatus queued; retry manual/admin.
            logger.warning("Gagal mengantre job %s ke broker Redis", job.id, exc_info=True)


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
    _: None = Depends(_upload_rate_limit),
) -> Job:
    _validate_options(scale, output_format)

    # FR-06/FR-11: cek slot (kuota gratis + kredit) SEBELUM membaca file —
    # user yang kehabisan tidak perlu mengunggah (hemat bandwidth & disk).
    if slots_available(current_user) <= 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Kuota gratis harian sudah habis ({settings.free_daily_quota} "
                "gambar/hari) dan saldo kredit kosong. Reset kuota besok "
                "(00:00 WIB) atau beli kredit di halaman Billing."
            ),
        )

    data, ext = await _read_and_validate(file)
    job_id = str(uuid4())
    original_path = save_upload(data=data, job_id=job_id, ext=ext)
    job = _build_job(
        job_id, current_user, original_path, file.filename or "gambar",
        scale, output_format, face_enhance, denoise, color_enhance,
    )
    # FR-06/FR-11: konsumsi 1 slot — kuota gratis dulu, kredit bila habis.
    # Di-refund sesuai sumber saat job gagal (app/tasks/enhance.py).
    _, credit_used = consume_slots(current_user, 1)
    job.uses_credit = credit_used > 0
    db.add(job)
    try:
        await db.commit()
    except Exception:
        # Hindari file yatim di disk bila insert DB gagal (P5 review).
        resolve(original_path).unlink(missing_ok=True)
        raise
    await db.refresh(job)

    await _enqueue(job, db)
    return job


@router.post(
    "/batch",
    response_model=JobListOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload beberapa gambar sekaligus (FR-12)",
)
async def create_batch_jobs(
    files: list[UploadFile] = File(...),
    scale: int = Form(2),
    output_format: str = Form("webp"),
    face_enhance: bool = Form(False),
    denoise: bool = Form(False),
    color_enhance: bool = Form(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(_upload_rate_limit),
) -> JobListOut:
    """Buat hingga `batch_max_files` job dalam satu request (FR-12).

    Validasi SEMUA file dulu (sebelum simpan apa pun) agar request gagal
    atomik: satu file tidak valid -> tidak ada job yang dibuat, kuota
    tidak terpotong. Kuota dicek untuk total batch (FR-06).
    """
    if not 1 <= len(files) <= settings.batch_max_files:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Jumlah file harus 1–{settings.batch_max_files}",
        )
    _validate_options(scale, output_format)

    if slots_available(current_user) < len(files):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Slot tidak cukup: batch ini butuh {len(files)} gambar, "
                f"tersedia {slots_available(current_user)} "
                "(kuota gratis + kredit). Beli kredit di halaman Billing."
            ),
        )

    # Fase 1: baca + validasi semua file (data disimpan di memori, maks
    # 10 x 10 MB = 100 MB — cukup untuk MVP).
    staged: list[tuple[bytes, str, str]] = []
    for f in files:
        data, ext = await _read_and_validate(f)
        staged.append((data, ext, f.filename or "gambar"))

    # Fase 2: simpan semua + insert job + konsumsi slot (satu transaksi).
    saved: list[str] = []
    jobs: list[Job] = []
    try:
        # Slot gratis dipakai lebih dulu; sisanya dari kredit (FR-11).
        free_used, _ = consume_slots(current_user, len(staged))
        for idx, (data, ext, name) in enumerate(staged):
            job_id = str(uuid4())
            original_path = save_upload(data=data, job_id=job_id, ext=ext)
            saved.append(original_path)
            job = _build_job(
                job_id, current_user, original_path, name,
                scale, output_format, face_enhance, denoise, color_enhance,
            )
            job.uses_credit = idx >= free_used
            db.add(job)
            jobs.append(job)
        await db.commit()
    except Exception:
        # Rollback transaksi + hapus file yatim bila commit gagal.
        await db.rollback()
        for path in saved:
            resolve(path).unlink(missing_ok=True)
        raise

    # Fase 3: antrekan semua job (eager inline di dev/test).
    for job in jobs:
        await _enqueue(job, db)

    return JobListOut(items=jobs, total=len(jobs))


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
