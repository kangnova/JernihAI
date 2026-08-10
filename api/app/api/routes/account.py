"""Hak subjek data (NFR-05 / UU PDP No. 27/2022) — export & hapus akun.

UU PDP memberi user hak akses dan hapus atas data pribadinya:
- `GET /account/export`    → salinan data pribadi (profil + riwayat job).
- `DELETE /account`        → hapus akun BESERTA seluruh data & file hasil
  (original + hasil), lalu log out. Idempoten setelah eksekusi.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.quota import quota_limit, quota_remaining
from app.core.security import clear_auth_cookie
from app.core.storage import resolve
from app.db.session import get_db
from app.models.job import Job
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/account", tags=["account"])


def _job_to_export(job: Job) -> dict:
    """Meta data job (TANPA konten biner) untuk keperluan export."""
    return {
        "id": job.id,
        "status": job.status,
        "scale": job.scale,
        "output_format": job.output_format,
        "face_enhance": job.face_enhance,
        "denoise": job.denoise,
        "color_enhance": job.color_enhance,
        "original_name": job.original_name,
        "original_path": job.original_path,
        "result_path": job.result_path,
        "error": job.error,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "original_deleted_at": (
            job.original_deleted_at.isoformat() if job.original_deleted_at else None
        ),
        "result_deleted_at": (
            job.result_deleted_at.isoformat() if job.result_deleted_at else None
        ),
    }


@router.get(
    "/export",
    summary="Ekspor data pribadi (hak akses — NFR-05)",
    response_class=JSONResponse,
)
async def export_data(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    """Salinan JSON data pribadi user: profil + meta riwayat job.

    Sesuai UU PDP, hasil export TIDAK memuat konten biner gambar — cukup
    metadata; file asli/olah tunduk retensi otomatis (FR-07).
    """
    jobs = (
        await db.execute(
            select(Job).where(Job.user_id == current_user.id).order_by(Job.created_at)
        )
    ).scalars()

    payload = {
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "user": {
            "id": current_user.id,
            "email": current_user.email,
            "name": current_user.name,
            "provider": current_user.provider,
            "privacy_consent_at": (
                current_user.privacy_consent_at.isoformat()
                if current_user.privacy_consent_at
                else None
            ),
            "created_at": current_user.created_at.isoformat()
            if current_user.created_at
            else None,
        },
        "free_quota": {
            "limit": quota_limit(),
            "used_today": quota_limit() - quota_remaining(current_user),
        },
        "jobs": [_job_to_export(job) for job in jobs],
    }

    return JSONResponse(
        content=payload,
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="jernihai-data-{current_user.id[:8]}.json"'
            )
        },
    )


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Hapus akun & seluruh data (hak hapus — NFR-05)",
)
async def delete_account(
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """Hapus akun beserta SEMUA data: job rows, file original & hasil di
    disk, lalu user. Cookie sesi dibersihkan (log out).

    Idempoten: bila user tidak lagi ada, `get_current_user` sudah menolak
    (401) — tidak ada partial state.
    """
    jobs = (
        await db.execute(select(Job).where(Job.user_id == current_user.id))
    ).scalars()

    deleted_files = 0
    for job in jobs:
        for attr in ("original_path", "result_path"):
            path = getattr(job, attr)
            if not path:
                continue
            try:
                resolve(path).unlink(missing_ok=True)
                deleted_files += 1
            except OSError as exc:  # noqa: BLE001 — lanjutkan hapus data lain
                logger.warning("Gagal hapus file %s saat hapus akun: %s", path, exc)

    await db.execute(delete(Job).where(Job.user_id == current_user.id))
    await db.delete(current_user)
    await db.commit()

    clear_auth_cookie(response)
    logger.info(
        "Akun %s dihapus (NFR-05) — %d file ikut terhapus",
        current_user.email,
        deleted_files,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
