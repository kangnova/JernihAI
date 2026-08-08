"""Task enhancement — mock pipeline Fase 1 (prd.md §12 & ADR-002).

Laptop dev TIDAK mendukung inference ML (CPU tanpa AVX2), jadi pipeline
ini memakai Pillow: resize LANCZOS + encode sesuai ADR-004 (WebP q90
default). Kontrak task (job_id -> update status di DB) sudah final —
penggantian ke Real-ESRGAN hanya terjadi di isi `_mock_enhance`, di
worker GPU (Vast.ai) tanpa menyentuh routes.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from app.core.config import settings
from app.db.session import async_session_factory
from app.models.job import Job, JobStatus
from app.tasks.worker import celery_app

# Kualitas encode sesuai ADR-004.
_ENCODE = {
    "webp": {"format": "WEBP", "quality": 90},
    "jpeg": {"format": "JPEG", "quality": 92},
    "png": {"format": "PNG"},
}


async def process_job(job_id: str) -> str | None:
    """Proses satu job: queued -> processing -> completed/failed.

    Dipanggil langsung (await) saat mode eager (dev/test tanpa Redis) atau
    dari task Celery di worker (lihat `process_enhancement`).

    Return status akhir job ("completed"/"failed") agar pemanggil (task
    Celery) bisa memutuskan retry — lihat TODO di `process_enhancement`.

    NOTE (NFR-03): belum ada timeout/heartbeat — job yang crash setelah
    commit `processing` bisa stuck di status itu. Gap ini ditutup di Fase 2
    (stale-check `updated_at` oleh worker/beat).
    """
    async with async_session_factory() as session:
        job = await session.get(Job, job_id)
        if job is None or job.status != JobStatus.QUEUED.value:
            return None
        job.status = JobStatus.PROCESSING.value
        await session.commit()
        try:
            job.result_path = _mock_enhance(job)
            job.status = JobStatus.COMPLETED.value
            job.finished_at = datetime.now(UTC)
        except Exception as exc:
            job.status = JobStatus.FAILED.value
            job.error = str(exc)[:500]
        await session.commit()
        return job.status


def _mock_enhance(job: Job) -> str:
    """MOCK pipeline: resize + encode (bukan ML).

    Fase 2/3: ganti isi fungsi ini dengan Real-ESRGAN + tiling + FP16
    (ADR-002) di worker GPU. Signature & return (path relatif hasil)
    dipertahankan agar routes tidak berubah.
    """
    src = Path(job.original_path)
    if not src.exists():
        raise FileNotFoundError(f"Original tidak ditemukan: {job.original_path}")

    with Image.open(src) as opened:
        img = opened.convert("RGB")
    w, h = img.size
    img = img.resize((w * job.scale, h * job.scale), Image.LANCZOS)

    ext = job.output_format
    rel = f"{settings.result_dir}/{job.id}.{ext}"
    dst = Path(rel)
    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst, **_ENCODE[ext])
    return rel


@celery_app.task(name="enhance.process", bind=True)
def process_enhancement(self, job_id: str) -> dict[str, str]:
    """Wrapper Celery (proses worker terpisah) — jalankan pipeline async.

    PENTING: jangan menyalakan `task_always_eager` Celery untuk task ini —
    `asyncio.run` akan crash bila dipanggil di dalam running event loop
    (request). Mode dev/test dipakai `settings.celery_task_always_eager`
    yang membuat route memanggil `process_job` secara langsung (await),
    bukan lewat task ini.

    TODO (NFR-03, Fase 2): retry otomatis untuk error transien. Retry
    hanya untuk status failed pada pipeline GPU nyata, dengan
    `self.retry(countdown=..., max_retries=2)` — bukan untuk mock ini.
    """
    status = asyncio.run(process_job(job_id))
    return {"job_id": job_id, "status": status or "skipped"}
