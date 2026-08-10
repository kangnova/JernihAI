"""Unduh pretrained weights (Real-ESRGAN + GFPGAN + deteksi wajah)
untuk worker GPU (idempotent).

Dipakai saat build image worker GPU (Dockerfile.worker) agar model sudah
ter-bake ke image, dan bisa dijalankan manual:

    python scripts/download_models.py            # ke settings.model_dir
    python scripts/download_models.py --dir /path  # ke folder tertentu

Yang diunduh:
- RealESRGAN_x4plus.pth (ADR-002) -> model_dir
- realesr-general-x4v3.pth + realesr-general-wdn-x4v3.pth (FR-09 denoise,
  DNI interpolasi) -> model_dir
- GFPGANv1.4.pth (FR-08 face enhance) -> <cwd>/gfpgan/weights/ (path yang
  dipakai kode produksi app/tasks/enhance.py; di container CWD=/app).
- Weight deteksi wajah facexlib (retinaface) -> <cwd>/facexlib/weights/,
  dipicu dengan memuat detektor sekali. Tanpa ini GFPGANer men-downloadnya
  via gdown (Google Drive) SAAT RUNTIME — rentan rate-limit/gagal di
  tengah pipeline job face_enhance. Membake saat build membuat worker
  berjalan tanpa internet.

Referensi weights: github.com/xinntao/Real-ESRGAN/releases (ADR-002,
v0.1.0 x4plus & v0.2.5.0 general); github.com/TencentARC/GFPGAN/releases
(FR-08).
"""

import argparse
import sys
import urllib.request
from pathlib import Path

# URL resmi dari rilis Real-ESRGAN (ADR-002: model utama x4plus; FR-09:
# general x4v3 + wdn-x4v3 untuk denoise DNI — sama seperti inference
# script realesrgan 0.3.0).
MODELS = {
    "RealESRGAN_x4plus.pth": (
        "https://github.com/xinntao/Real-ESRGAN/releases/download/"
        "v0.1.0/RealESRGAN_x4plus.pth"
    ),
    "realesr-general-x4v3.pth": (
        "https://github.com/xinntao/Real-ESRGAN/releases/download/"
        "v0.2.5.0/realesr-general-x4v3.pth"
    ),
    "realesr-general-wdn-x4v3.pth": (
        "https://github.com/xinntao/Real-ESRGAN/releases/download/"
        "v0.2.5.0/realesr-general-wdn-x4v3.pth"
    ),
}
# FR-08: GFPGAN untuk restorasi wajah (dipakai GFPGANer di
# app/tasks/enhance.py saat face_enhance=True). Disimpan di `gfpgan/weights`
# relatif CWD.
GFPGAN_MODEL = "GFPGANv1.4.pth"
GFPGAN_URL = (
    "https://github.com/TencentARC/GFPGAN/releases/download/"
    "v1.3.0/GFPGANv1.4.pth"
)
# File rusak/parsial biasanya < 1 MB; cukup cek ukuran untuk idempotent.
_MIN_VALID_BYTES = 1_000_000


def default_dir() -> Path:
    try:
        from app.core.config import settings
    except ImportError:
        return Path("storage/models")
    return Path(settings.model_dir)


def _download(name: str, url: str, dst: Path, failed: list[bool]) -> None:
    """Unduh satu model idempotent; tandai `failed` bila error."""
    if dst.exists() and dst.stat().st_size >= _MIN_VALID_BYTES:
        print(f"SKIP  {name} (sudah ada, {dst.stat().st_size / 1e6:.1f} MB)")
        return
    print(f"UNDUH {name} -> {dst} ...")
    try:
        urllib.request.urlretrieve(url, dst)
        print(f"OK    {name} ({dst.stat().st_size / 1e6:.1f} MB)")
    except Exception as exc:  # noqa: BLE001 — laporkan lalu lanjut model lain
        failed[0] = True
        print(f"GAGAL {name}: {exc}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=None, help="Folder tujuan Real-ESRGAN")
    args = parser.parse_args()
    model_dir = args.dir or default_dir()
    model_dir.mkdir(parents=True, exist_ok=True)

    failed: list[bool] = [False]
    for name, url in MODELS.items():
        _download(name, url, model_dir / name, failed)

    # FR-08: GFPGAN ditaruh di `gfpgan/weights` RELATIF CWD — RealESRGANer
    # memuatnya dengan path hardcoded `gfpgan/weights/GFPGANv1.4.pth` saat
    # face_enhance=True, jadi lokasinya TIDAK boleh mengikuti --dir (yang
    # hanya untuk model Real-ESRGAN). Di container CWD=/app.
    gfpgan_dir = Path("gfpgan") / "weights"
    gfpgan_dir.mkdir(parents=True, exist_ok=True)
    _download(GFPGAN_MODEL, GFPGAN_URL, gfpgan_dir / GFPGAN_MODEL, failed)

    # FR-08: bake weight deteksi wajah facexlib supaya face_enhance tidak
    # butuh internet saat runtime (gdown rentan gagal/rate-limit). Memuat
    # detektor sekali akan men-download weights (retinaface) ke
    # `facexlib/weights` relatif CWD. Aman bila facexlib belum terpasang
    # (dev laptop) — lewati dengan warning.
    try:
        from facexlib.utils.face_detection import init_detection_model

        init_detection_model("retinaface_resnet50", device="cpu")
        print("OK    facexlib/retinaface (weight deteksi wajah ter-bake)")
    except Exception as exc:  # noqa: BLE001 — bukan blocker untuk model lain
        print(f"SKIP  facexlib/retinaface: {exc}", file=sys.stderr)

    return 1 if failed[0] else 0


if __name__ == "__main__":
    raise SystemExit(main())
