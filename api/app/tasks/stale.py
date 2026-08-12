"""Stale-check job (NFR-03) — pulihkan job yang stuck di status `processing`.

Masalah: worker GPU bisa crash/hang di tengah pipeline (mis. OOM, bug model)
SETELAH job di-commit berstatus `processing`. Tanpa mekanisme pemulihan, job
tersebut stuck selamanya: user tidak bisa mengunduh, kuota hangus, dan
retensi FR-07 tidak pernah menghapus original-nya (retensi hanya menyentuh
job `completed`/`failed` — lihat app/tasks/retention.py) → bocor disk.

Solusi: sweep berkala (`recover_stale_jobs`, dijadwalkan Celery Beat) yang
menandai job `processing` yang `updated_at`-nya lebih tua dari
`job_stale_minutes` menjadi `failed` + error jelas + refund sesuai sumber
pembayaran (FR-06/FR-11): kuota gratis untuk job gratis, kredit untuk job
berbayar. Setelah itu retensi normal bisa membersihkan filenya.

Keputusan desain (NFR-03):
- Retry otomatis 2x di task Celery hanya menutupi exception yang DITANGKAP
  di dalam `process_job` (pipeline error, model error). Worker yang CRASH
  (proses mati: OOM/segfault) tidak melewati jalur retry — pesannya di-redeliver
  broker (acks_late) tapi `process_job` menolak status `processing`, sehingga
  hanya stale-check yang memulihkannya menjadi `failed` + refund (user coba
  lagi). Ini gap semantik yang disengaja untuk MVP.
- RACE yang diketahui: stale-check bisa menandai job `failed` saat worker
  masih menjalankan pipeline (hanya lambat, bukan mati). Bila pipeline
  selesai setelahnya, `process_job` menimpa status jadi `completed` — kuota
  sudah ter-refund (user "gratis") dan `job.error` sudah di-sapu ulang ke
  None di branch sukses. Threshold 30 menit vs pipeline ~10 detik membuat
  kejadian ini praktis nol, tapi tanpa heartbeat worker gap ini ada.
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.core.config import settings
from app.core.quota import refund_credit, refund_quota
from app.db.session import async_session_factory
from app.models.job import Job, JobStatus
from app.models.user import User
from app.tasks.worker import celery_app

logger = logging.getLogger(__name__)


async def recover_stale_jobs(now: datetime | None = None) -> dict[str, int]:
    """Tandai job `processing` yang hang menjadi `failed` + refund kuota.

    Return jumlah job yang dipulihkan. Idempoten: job yang sudah `failed`
    tidak disentuh.
    """
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(minutes=settings.job_stale_minutes)

    stats = {"recovered": 0}

    async with async_session_factory() as session:
        rows = await session.execute(
            select(Job).where(
                Job.status == JobStatus.PROCESSING.value,
                Job.updated_at < cutoff,
            )
        )
        for job in rows.scalars():
            # Tandai failed secara ATOMIK (guard status, pola Fase 3 yang
            # sama dengan _fail_job di enhance.py): hanya sweeper yang
            # menang UPDATE yang refund — dua sweeper konkuren (mis. beat
            # ke-dobel) tidak bisa refund ganda. Idempoten: job yang sudah
            # diubah pihak lain -> rowcount 0 -> dilewati.
            result = await session.execute(
                update(Job)
                .where(
                    Job.id == job.id,
                    Job.status == JobStatus.PROCESSING.value,
                )
                .values(
                    status=JobStatus.FAILED.value,
                    error=(
                        "Waktu pemrosesan habis — job tersangkut di status "
                        f"processing lebih dari {settings.job_stale_minutes} "
                        "menit (NFR-03). Silakan coba lagi."
                    ),
                )
            )
            if result.rowcount != 1:
                continue
            user = await session.get(User, job.user_id)
            if user is not None:
                # FR-11: refund SESUAI SUMBER — job berbayar mengembalikan
                # kredit (bukan kuota gratis), job gratis sebaliknya.
                if job.uses_credit:
                    refund_credit(user)
                else:
                    refund_quota(user)
            stats["recovered"] += 1

        await session.commit()

    if stats["recovered"]:
        logger.warning("Stale-check: %s job processing dipulihkan jadi failed", stats["recovered"])
    return stats


@celery_app.task(name="jobs.recover_stale", ignore_result=True)
def recover_stale_jobs_task() -> dict[str, int]:
    """Wrapper Celery — jalankan stale-check async di worker beat."""
    import asyncio

    return asyncio.run(recover_stale_jobs())
