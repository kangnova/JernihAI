"""Task enhancement — pipeline Real-ESRGAN asli (Fase 2, ADR-002) + fallback mock.

Laptop dev (CPU A8-7410 tanpa AVX2) TIDAK bisa menjalankan PyTorch, jadi
backend di sini memilih otomatis (prd.md §12):

- `enhance_backend=real`  → Real-ESRGAN x4plus via `RealESRGANer`
  (tiling 512, FP16 di CUDA) — dipakai worker GPU (Vast.ai, ADR-001).
- `enhance_backend=mock`  → pipeline Pillow (resize + encode ADR-004) —
  dipakai dev lokal & unit test.
- `enhance_backend=auto` (default) → real bila model+torch tersedia,
  selain itu fallback mock dengan warning.

Import numpy/torch/realesrgan sengaja LAZY (di dalam fungsi) agar image
worker dev yang ramping (tanpa torch) tetap bisa jalan normal.
"""

import asyncio
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from app.core.config import settings
from app.db.session import async_session_factory
from app.models.job import Job, JobStatus
from app.tasks.worker import celery_app

logger = logging.getLogger(__name__)

# Kualitas encode sesuai ADR-004.
_ENCODE = {
    "webp": {"format": "WEBP", "quality": 90},
    "jpeg": {"format": "JPEG", "quality": 92},
    "png": {"format": "PNG"},
}

# --- Real-ESRGAN: loader lazy + cache global (sekali per proses worker) ---

_upsampler = None
_upsampler_error: str | None = None
_upsampler_tried = False
# Amankan lazy-load bila pool Celery suatu saat bukan `solo` / dipanggil
# dari beberapa thread (double-checked locking).
_upsampler_lock = threading.Lock()


def _get_upsampler():
    """Muat `RealESRGANer` sekali per proses; None bila tidak tersedia.

    Cache global penting: memuat model (RRDBNet 16.7M params) per job akan
    sangat lambat. Dipanggil di thread worker Celery (pool solo), aman.
    """
    global _upsampler, _upsampler_error, _upsampler_tried
    if _upsampler_tried:
        return _upsampler
    with _upsampler_lock:
        if _upsampler_tried:
            return _upsampler
        _upsampler_tried = True
    try:
        import torch  # noqa: F401 — pastikan torch terinstal
        from basicsr.archs.rrdbnet_arch import RRDBNet
        from realesrgan import RealESRGANer

        if settings.model_device == "auto":
            device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        else:
            device = torch.device(settings.model_device)
        half = bool(settings.half_precision) and device.type == "cuda"

        model = RRDBNet(
            num_in_ch=3, num_out_ch=3, scale=4,
            num_feat=64, num_block=23, num_grow_ch=32,
        )
        model_path = Path(settings.model_dir) / settings.realesrgan_model
        if not model_path.exists():
            raise FileNotFoundError(
                f"Weights tidak ditemukan: {model_path}. Jalankan "
                "`python scripts/download_models.py` (atau unduh manual)."
            )

        _upsampler = RealESRGANer(
            scale=4,
            model_path=str(model_path),
            model=model,
            tile=settings.tile_size,
            tile_pad=settings.tile_pad,
            pre_pad=settings.pre_pad,
            half=half,
            device=device,
        )
        logger.info(
            "Backend real siap: device=%s, half=%s, tile=%s",
            device, half, settings.tile_size,
        )
    except Exception as exc:  # noqa: BLE001 — semua kegagalan backend -> fallback
        _upsampler_error = f"Backend real tidak tersedia: {exc}"
        logger.warning(_upsampler_error)
        _upsampler = None
    return _upsampler


def _resolve_backend() -> str:
    """Pilih backend sesuai config: auto/real/mock."""
    if settings.enhance_backend != "auto":
        return settings.enhance_backend
    return "real" if _get_upsampler() is not None else "mock"


def _effective_outscale(job: Job) -> int:
    """Scale efektif dengan batas output maks (ADR-004: 7680×4320).

    Request scale bisa menghasilkan output > batas (mis. input 4K × 4x).
    Kembalikan scale terbesar yang tetap memenuhi batas sisi terpanjang.

    Catatan: bila INPUT sudah melebihi batas, outscale = 1 (tanpa upscale)
    — input tidak diperkecil diam-diam; hasil = ukuran input.
    """
    with Image.open(job.original_path) as opened:
        longest = max(opened.size)
    if longest * job.scale <= settings.max_output_longest:
        return job.scale
    return max(1, settings.max_output_longest // longest)


async def process_job(job_id: str) -> str | None:
    """Proses satu job: queued -> processing -> completed/failed.

    Dipanggil langsung (await) saat mode eager (dev/test tanpa Redis) atau
    dari task Celery di worker (lihat `process_enhancement`).

    Return status akhir job ("completed"/"failed") agar pemanggil (task
    Celery) bisa memutuskan retry — lihat TODO di `process_enhancement`.

    NOTE (NFR-03): belum ada timeout/heartbeat — job yang crash setelah
    commit `processing` bisa stuck di status itu. Gap ini ditutup dengan
    stale-check `updated_at` oleh worker/beat (roadmap Fase 2/3).
    """
    async with async_session_factory() as session:
        job = await session.get(Job, job_id)
        if job is None or job.status != JobStatus.QUEUED.value:
            return None
        job.status = JobStatus.PROCESSING.value
        await session.commit()
        try:
            job.result_path = _enhance(job)
            job.status = JobStatus.COMPLETED.value
            job.finished_at = datetime.now(UTC)
        except Exception as exc:
            job.status = JobStatus.FAILED.value
            job.error = str(exc)[:500]
        await session.commit()
        return job.status


def _enhance(job: Job) -> str:
    """Dispatcher backend. Signature & return (path relatif hasil) konsisten."""
    backend = _resolve_backend()
    if backend == "real":
        return _enhance_real(job)
    if backend == "mock":
        return _enhance_mock(job)
    # Backend tidak dikenal (salah konfigurasi ENHANCE_BACKEND) -> gagal keras.
    raise RuntimeError(f"enhance_backend tidak dikenal: {backend!r}")


def _encode_and_save(image: Image.Image, job: Job) -> str:
    """Encode sesuai ADR-004 + simpan; return path relatif hasil.

    JPEG tidak mendukung alpha -> konversi RGB bila perlu (WebP/PNG bisa
    mempertahankan alpha).
    """
    ext = job.output_format
    if ext == "jpeg" and image.mode != "RGB":
        image = image.convert("RGB")
    rel = f"{settings.result_dir}/{job.id}.{ext}"
    dst = Path(rel)
    dst.parent.mkdir(parents=True, exist_ok=True)
    image.save(dst, **_ENCODE[ext])
    return rel


def _enhance_mock(job: Job) -> str:
    """MOCK pipeline: resize + encode (bukan ML) — dev lokal & test (prd §12)."""
    src = Path(job.original_path)
    if not src.exists():
        raise FileNotFoundError(f"Original tidak ditemukan: {job.original_path}")

    outscale = _effective_outscale(job)
    with Image.open(src) as opened:
        img = opened.convert("RGB")
    w, h = img.size
    img = img.resize((w * outscale, h * outscale), Image.LANCZOS)
    return _encode_and_save(img, job)


def _enhance_real(job: Job) -> str:
    """REAL pipeline: Real-ESRGAN x4plus (ADR-002) + encode ADR-004.

    - Input Pillow -> numpy HWC uint8 (RGBA dipertahankan: RealESRGANer
      meng-upscale alpha channel sendiri).
    - `outscale` = scale efektif (2x/4x, dibatasi max output 7680×4320).
    - Tiling + FP16 sudah dikonfigurasi di `_get_upsampler`.
    """
    upsampler = _get_upsampler()
    if upsampler is None:
        raise RuntimeError(_upsampler_error or "Backend real tidak tersedia")

    # Import lazy: numpy hanya ada di worker GPU (extra `gpu`).
    import numpy as np

    src = Path(job.original_path)
    if not src.exists():
        raise FileNotFoundError(f"Original tidak ditemukan: {job.original_path}")

    outscale = _effective_outscale(job)
    with Image.open(src) as opened:
        img = opened.convert("RGBA" if "A" in opened.getbands() else "RGB")
    output, _ = upsampler.enhance(np.array(img), outscale=outscale)
    return _encode_and_save(Image.fromarray(output), job)


@celery_app.task(name="enhance.process", bind=True)
def process_enhancement(self, job_id: str) -> dict[str, str]:
    """Wrapper Celery (proses worker terpisah) — jalankan pipeline async.

    PENTING: jangan menyalakan `task_always_eager` Celery untuk task ini —
    `asyncio.run` akan crash bila dipanggil di dalam running event loop
    (request). Mode dev/test dipakai `settings.celery_task_always_eager`
    yang membuat route memanggil `process_job` secara langsung (await),
    bukan lewat task ini.

    TODO (NFR-03): retry otomatis untuk error transien — hanya untuk
    status failed pipeline GPU nyata, dengan
    `self.retry(countdown=..., max_retries=2)`.
    """
    status = asyncio.run(process_job(job_id))
    return {"job_id": job_id, "status": status or "skipped"}
