"""Retensi data otomatis (FR-07 / UU PDP) — hapus file kedaluwarsa.

Jadwal: dijalankan Celery Beat setiap `retention_purge_interval_minutes`
(lihat `beat_schedule` di app/tasks/worker.py). Fungsi inti `purge_expired`
bersifat async dan bisa dipanggil langsung (test) tanpa broker Redis.

Aturan (prd.md FR-07):
- Original dihapus setelah `retention_original_hours` (24 jam) sejak dibuat,
  hanya untuk job yang sudah selesai/gagal (job queued/processing masih butuh
  file-nya — dilindungi agar tidak dihapus saat sedang diproses).
- Hasil proses dihapus setelah `retention_result_days` (7 hari) sejak selesai.
- Path di DB dipertahankan untuk audit; kolom `*_deleted_at` menandai
  penghapusan sehingga endpoint download bisa menjawab dengan jelas.

Keamanan: file hanya dihapus bila benar-benar berada di dalam `upload_dir`
atau `result_dir` (guard path traversal).
"""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.db.session import async_session_factory
from app.models.job import Job, JobStatus
from app.tasks.worker import celery_app

logger = logging.getLogger(__name__)

_DONE_STATUSES = (JobStatus.COMPLETED.value, JobStatus.FAILED.value)


def _unlink_if_inside(path: str | None, base_dir: str) -> bool:
    """Hapus file bila path berada di dalam base_dir (guard traversal)."""
    if not path:
        return False
    try:
        resolved = Path(path).resolve()
        base = Path(base_dir).resolve()
        if resolved.is_relative_to(base) and resolved.is_file():
            resolved.unlink(missing_ok=True)
            return True
    except OSError as exc:  # noqa: BLE001 — file lock dll. tidak menghentikan sweep
        logger.warning("Gagal hapus %s (retensi): %s", path, exc)
    return False


async def purge_expired(now: datetime | None = None) -> dict[str, int]:
    """Hapus original > 24 jam & hasil > 7 hari; return jumlah file terhapus.

    Idempoten & aman dipanggil berulang (task beat berjalan berkala).
    """
    now = now or datetime.now(UTC)
    original_cutoff = now - timedelta(hours=settings.retention_original_hours)
    result_cutoff = now - timedelta(days=settings.retention_result_days)

    stats = {"original_deleted": 0, "result_deleted": 0}

    async with async_session_factory() as session:
        # --- Original kedaluwarsa (hanya job yang tidak lagi diproses) ---
        rows = await session.execute(
            select(Job).where(
                Job.original_deleted_at.is_(None),
                Job.status.in_(_DONE_STATUSES),
                Job.created_at < original_cutoff,
            )
        )
        for job in rows.scalars():
            if _unlink_if_inside(job.original_path, settings.upload_dir):
                job.original_deleted_at = now
                stats["original_deleted"] += 1

        # --- Hasil kedaluwarsa (hanya job selesai dengan file hasil) ---
        rows = await session.execute(
            select(Job).where(
                Job.result_deleted_at.is_(None),
                Job.status == JobStatus.COMPLETED.value,
                Job.finished_at.is_not(None),
                Job.finished_at < result_cutoff,
            )
        )
        for job in rows.scalars():
            if _unlink_if_inside(job.result_path, settings.result_dir):
                job.result_deleted_at = now
                stats["result_deleted"] += 1

        await session.commit()

    if any(stats.values()):
        logger.info("Retensi: %s", stats)
    return stats


# --- Task Celery (dijadwalkan beat_schedule di worker.py) ---


@celery_app.task(name="retention.purge_expired", ignore_result=True)
def purge_expired_task() -> dict[str, int]:
    """Wrapper Celery — jalankan purge async di worker beat."""
    import asyncio

    return asyncio.run(purge_expired())
