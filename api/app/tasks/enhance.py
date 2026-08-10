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

from PIL import Image, ImageEnhance, ImageFilter

from app.core.config import settings
from app.core.quota import refund_quota
from app.db.session import async_session_factory
from app.models.job import Job, JobStatus
from app.models.user import User
from app.tasks.worker import celery_app

logger = logging.getLogger(__name__)

# Kualitas encode sesuai ADR-004.
_ENCODE = {
    "webp": {"format": "WEBP", "quality": 90},
    "jpeg": {"format": "JPEG", "quality": 92},
    "png": {"format": "PNG"},
}

# --- Real-ESRGAN: loader lazy + cache global (sekali per proses worker) ---
# Cache dibedakan per mode: `x4plus` (upscale biasa) dan `general` (FR-09
# denoise via DNI). GFPGANer (FR-08) di-cache per outscale.

_upsamplers: dict[str, object] = {}
_upsampler_error: str | None = None
_upsampler_tried = False
# Cache GFPGANer per (outscale, upsampler): `bg_upsampler` ter-bake saat
# konstruksi, jadi dua mode upsampler (x4plus vs general/denoise) TIDAK
# boleh berbagi instance yang sama (P9 review FR-09).
_face_enhancers: dict[tuple[int, object], object] = {}
# Amankan lazy-load bila pool Celery suatu saat bukan `solo` / dipanggil
# dari beberapa thread (double-checked locking).
_upsampler_lock = threading.Lock()


def _device_and_half() -> tuple:
    """Resolve device (cuda/cpu) + flag FP16 dari settings."""
    import torch  # noqa: F401 — pastikan torch terinstal

    if settings.model_device == "auto":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device(settings.model_device)
    half = bool(settings.half_precision) and device.type == "cuda"
    return device, half


def _load_x4plus_upsampler():
    """RealESRGANer x4plus (RRDBNet) — mode default (ADR-002)."""
    from basicsr.archs.rrdbnet_arch import RRDBNet
    from realesrgan import RealESRGANer

    device, half = _device_and_half()
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
    return RealESRGANer(
        scale=4,
        model_path=str(model_path),
        model=model,
        tile=settings.tile_size,
        tile_pad=settings.tile_pad,
        pre_pad=settings.pre_pad,
        half=half,
        device=device,
    )


def _load_general_upsampler():
    """RealESRGANer realesr-general-x4v3 + wdn (FR-09 denoise, DNI).

    Denoise strength dikontrol via Deep Network Interpolation antara model
    normal dan versi with-denoise (wdn): `dni_weight = [denoise_strength,
    1 - denoise_strength]` — sama seperti inference_realesrgan.py 0.3.0
    dengan flag `-dn`.
    """
    from realesrgan import RealESRGANer
    from realesrgan.archs.srvgg_arch import SRVGGNetCompact

    device, half = _device_and_half()
    model = SRVGGNetCompact(
        num_in_ch=3, num_out_ch=3, num_feat=64,
        num_conv=32, upscale=4, act_type="prelu",
    )
    model_dir = Path(settings.model_dir)
    paths = [
        model_dir / settings.realesrgan_general_model,
        model_dir / settings.realesrgan_general_wdn_model,
    ]
    missing = [p for p in paths if not p.exists()]
    if missing:
        raise FileNotFoundError(
            f"Weights denoise tidak ditemukan: {[str(p) for p in missing]}. "
            "Jalankan `python scripts/download_models.py`."
        )
    strength = max(0.0, min(1.0, settings.denoise_strength))
    return RealESRGANer(
        scale=4,
        model_path=[str(p) for p in paths],
        dni_weight=[strength, 1 - strength],
        model=model,
        tile=settings.tile_size,
        tile_pad=settings.tile_pad,
        pre_pad=settings.pre_pad,
        half=half,
        device=device,
    )


def _get_upsampler(denoise: bool = False):
    """Muat `RealESRGANer` (mode denoise atau default) sekali per proses.

    Cache global penting: memuat model (RRDBNet 16.7M params) per job akan
    sangat lambat. Dipanggil di thread worker Celery (pool solo), aman.
    Return None bila backend tidak tersedia (fallback ke mock).
    """
    global _upsampler_error, _upsampler_tried
    key = "general" if denoise else "x4plus"
    if key in _upsamplers:
        return _upsamplers[key]
    with _upsampler_lock:
        if key in _upsamplers:
            return _upsamplers[key]
        _upsampler_tried = True
        try:
            _upsamplers[key] = (
                _load_general_upsampler() if denoise else _load_x4plus_upsampler()
            )
            logger.info("Backend real (%s) siap", key)
        except Exception as exc:  # noqa: BLE001 — semua kegagalan -> fallback
            _upsampler_error = f"Backend real ({key}) tidak tersedia: {exc}"
            logger.warning(_upsampler_error)
            _upsamplers[key] = None
    return _upsamplers[key]


def _get_face_enhancer(upsampler, outscale: int):
    """GFPGANer (FR-08) dengan background upsampler RealESRGANer; cache per (outscale, upsampler).

    Di realesrgan 0.3.0, `RealESRGANer.enhance` TIDAK punya parameter
    `face_enhance` — restorasi wajah dilakukan via `GFPGANer` terpisah
    dengan `bg_upsampler` (pola inference_realesrgan.py v0.3.0). Model
    GFPGANv1.4.pth dibake di `gfpgan/weights` (scripts/download_models.py).

    Kunci cache memuat objek `upsampler` (identitas) karena `bg_upsampler`
    ter-bake saat konstruksi: job denoise (upsampler general) dan job biasa
    (upsampler x4plus) pada outscale sama TIDAK boleh berbagi instance.
    """
    cache_key = (outscale, upsampler)
    global _face_enhancers
    if cache_key in _face_enhancers:
        return _face_enhancers[cache_key]
    with _upsampler_lock:
        if cache_key in _face_enhancers:
            return _face_enhancers[cache_key]
        from gfpgan import GFPGANer

        model_path = Path("gfpgan") / "weights" / settings.gfpgan_model
        if not model_path.exists():
            raise FileNotFoundError(
                f"Weights GFPGAN tidak ditemukan: {model_path}. Jalankan "
                "`python scripts/download_models.py`."
            )
        _face_enhancers[cache_key] = GFPGANer(
            model_path=str(model_path),
            upscale=outscale,
            arch="clean",
            channel_multiplier=2,
            bg_upsampler=upsampler,
            device=upsampler.device,
        )
        logger.info("GFPGANer siap (outscale=%s)", outscale)
    return _face_enhancers[cache_key]


def _resolve_backend(denoise: bool = False) -> str:
    """Pilih backend sesuai config: auto/real/mock.

    Mode `auto` memeriksa ketersediaan model SESUAI kebutuhan job:
    denoise memakai model `general` (FR-09, x4v3+wdn), selain itu
    `x4plus` (ADR-002). Bila model yang dibutuhkan tidak ada -> fallback
    mock (prd.md §12).
    """
    if settings.enhance_backend != "auto":
        return settings.enhance_backend
    return "real" if _get_upsampler(denoise=denoise) is not None else "mock"


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


async def process_job(
    job_id: str,
    *,
    force_retry: bool = False,
    refund_on_fail: bool = True,
) -> str | None:
    """Proses satu job: queued -> processing -> completed/failed.

    Dipanggil langsung (await) saat mode eager (dev/test tanpa Redis) atau
    dari task Celery di worker (lihat `process_enhancement`).

    Parameter NFR-03:
    - `force_retry=True`: izinkan memproses ulang job berstatus `failed`
      (dipakai eksekusi retry Celery setelah percobaan pertama gagal).
    - `refund_on_fail=False`: jangan refund kuota saat gagal — dipakai
      percobaan retry yang masih punya sisa kesempatan; refund hanya di
      percobaan TERAKHIR (mencegah refund berlipat untuk 1 job).

    Return status akhir job ("completed"/"failed") agar pemanggil (task
    Celery) bisa memutuskan retry — lihat `process_enhancement`.

    NOTE (NFR-03): job yang crash SETELAH commit `processing` tetap bisa
    stuck di status itu — ditutup oleh stale-check `recover_stale_jobs`
    (app/tasks/stale.py) yang menandainya `failed` + refund.
    """
    async with async_session_factory() as session:
        job = await session.get(Job, job_id)
        if job is None:
            return None
        # QUEUED selalu boleh diproses; FAILED hanya bila force_retry.
        if job.status != JobStatus.QUEUED.value and not (
            force_retry and job.status == JobStatus.FAILED.value
        ):
            return None
        job.status = JobStatus.PROCESSING.value
        job.error = None  # bersihkan error percobaan sebelumnya saat retry
        await session.commit()
        try:
            job.result_path = _enhance(job)
            job.status = JobStatus.COMPLETED.value
            job.finished_at = datetime.now(UTC)
            job.error = None  # hygiene: sapu pesan stale-check bila ada race
        except Exception as exc:
            job.status = JobStatus.FAILED.value
            job.error = str(exc)[:500]
            if refund_on_fail:
                # FR-06: job gagal TIDAK menghabiskan kuota — kembalikan 1
                # jatah (floor 0), digabung dalam transaksi status failed.
                user = await session.get(User, job.user_id)
                if user is not None:
                    refund_quota(user)
        await session.commit()
        return job.status


def _enhance(job: Job) -> str:
    """Dispatcher backend. Signature & return (path relatif hasil) konsisten."""
    backend = _resolve_backend(denoise=bool(job.denoise))
    if backend == "real":
        return _enhance_real(job)
    if backend == "mock":
        return _enhance_mock(job)
    # Backend tidak dikenal (salah konfigurasi ENHANCE_BACKEND) -> gagal keras.
    raise RuntimeError(f"enhance_backend tidak dikenal: {backend!r}")


def _color_enhance(img: Image.Image, strength: float) -> Image.Image:
    """Pra-pemrosesan warna ringan (FR-09) — murni Pillow, tanpa GPU.

    `strength=1.0` netral (tidak mengubah apa pun); >1.0 mempertegas
    warna (saturasi paling kuat, kontras & brightness sedikit) untuk foto
    lama yang pudar. Dipakai konsisten di backend mock & real.
    """
    s = max(0.0, strength)
    img = ImageEnhance.Color(img).enhance(s)
    img = ImageEnhance.Contrast(img).enhance(1 + (s - 1) * 0.5)
    img = ImageEnhance.Brightness(img).enhance(1 + (s - 1) * 0.2)
    return img


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
    """MOCK pipeline: efek ringan + resize + encode (bukan ML) — dev & test.

    FR-08: face_enhance TIDAK diproses di mock (stub dev). Bila backend
    auto jatuh ke mock, log warning agar user tidak terkecoh hasil tanpa
    restorasi wajah.
    FR-09: denoise (MedianFilter) & color enhance diaplikasikan dengan
    efek ringan agar toggle tetap terasa efeknya di dev (bukan ML asli).
    """
    if job.face_enhance:
        logger.warning(
            "Job %s: face_enhance diminta tapi backend mock mengabaikannya "
            "(butuh worker GPU / ENHANCE_BACKEND=real)",
            job.id,
        )
    src = Path(job.original_path)
    if not src.exists():
        raise FileNotFoundError(f"Original tidak ditemukan: {job.original_path}")

    outscale = _effective_outscale(job)
    with Image.open(src) as opened:
        img = opened.convert("RGB")
    if job.denoise:
        img = img.filter(ImageFilter.MedianFilter(size=3))
    if job.color_enhance:
        img = _color_enhance(img, settings.color_enhance_strength)
    w, h = img.size
    img = img.resize((w * outscale, h * outscale), Image.LANCZOS)
    return _encode_and_save(img, job)


def _enhance_real(job: Job) -> str:
    """REAL pipeline: Real-ESRGAN (ADR-002) + encode ADR-004.

    - Kontrak numpy RealESRGANer/GFPGANer v0.3.0: input & output = uint8
      HWC BGR (konvensi OpenCV). Pillow RGB dikonversi ke BGR sebelum
      masuk; hasil BGR dikonversi balik ke RGB.
    - FR-09: `denoise=True` memakai model realesr-general-x4v3 + wdn
      (DNI interpolasi, `_load_general_upsampler`); `color_enhance=True`
      menjalankan pra-pemrosesan warna Pillow (`_color_enhance`).
    - FR-08: `face_enhance=True` memanggil `GFPGANer` TERPISAH dengan
      `bg_upsampler` — realesrgan 0.3.0 TIDAK punya param `face_enhance`
      di `RealESRGANer.enhance()` (lihat `_get_face_enhancer`).
    - Alpha: model hanya menerima 3 kanal (BGR) — kanal alpha di-upscale
      terpisah (LANCZOS) lalu disatukan kembali ke hasil.
    - `outscale` = scale efektif (2x/4x, dibatasi max output 7680×4320);
      tiling + FP16 dikonfigurasi di `_get_upsampler`.
    """
    upsampler = _get_upsampler(denoise=bool(job.denoise))
    if upsampler is None:
        raise RuntimeError(_upsampler_error or "Backend real tidak tersedia")

    # Import lazy: numpy hanya ada di worker GPU (extra `gpu`).
    import numpy as np

    src = Path(job.original_path)
    if not src.exists():
        raise FileNotFoundError(f"Original tidak ditemukan: {job.original_path}")

    outscale = _effective_outscale(job)
    with Image.open(src) as opened:
        alpha = opened.getchannel("A") if "A" in opened.getbands() else None
        img = opened.convert("RGB")

    if job.color_enhance:
        img = _color_enhance(img, settings.color_enhance_strength)

    # RGB (Pillow) -> BGR (kontrak RealESRGANer/GFPGANer v0.3.0).
    img_bgr = np.asarray(img)[:, :, ::-1].copy()

    if job.face_enhance:
        face_enhancer = _get_face_enhancer(upsampler, outscale)
        _, _, output_bgr = face_enhancer.enhance(
            img_bgr,
            has_aligned=False,
            only_center_face=False,
            paste_back=True,
        )
    else:
        output_bgr, _ = upsampler.enhance(img_bgr, outscale=outscale)

    # BGR -> RGB; alpha di-upscale terpisah (model 3 kanal saja).
    output = Image.fromarray(output_bgr[:, :, ::-1].copy())
    if alpha is not None:
        output.putalpha(alpha.resize(output.size, Image.LANCZOS))
    return _encode_and_save(output, job)


@celery_app.task(
    name="enhance.process",
    bind=True,
    max_retries=settings.job_max_retries,
    default_retry_delay=30,
)
def process_enhancement(self, job_id: str) -> dict[str, str]:
    """Wrapper Celery (proses worker terpisah) — jalankan pipeline async.

    NFR-03 — retry otomatis (max `settings.job_max_retries` = 2):
    - Percobaan ke-1 (`retries=0`): job QUEUED, `refund_on_fail=False`.
    - Percobaan retry (`retries>0`): job sudah `failed` -> `force_retry`
      memproses ulang; `refund_on_fail` tetap False sampai percobaan terakhir.
    - Percobaan terakhir (`retries == max_retries`): bila gagal lagi, kuota
      direfund (FR-06) dan job tetap `failed` — tidak retry lagi.

    PENTING: jangan menyalakan `task_always_eager` Celery untuk task ini —
    `asyncio.run` akan crash bila dipanggil di dalam running event loop
    (request). Mode dev/test dipakai `settings.celery_task_always_eager`
    yang membuat route memanggil `process_job` secara langsung (await),
    bukan lewat task ini.
    """
    retries = self.request.retries
    max_retries = self.max_retries
    status = asyncio.run(
        process_job(
            job_id,
            force_retry=retries > 0,
            refund_on_fail=retries >= max_retries,
        )
    )
    if status == JobStatus.FAILED.value and retries < max_retries:
        # Sisa percobaan masih ada -> retry dengan backoff eksponensial.
        raise self.retry(countdown=30 * (2**retries))
    return {"job_id": job_id, "status": status or "skipped"}
