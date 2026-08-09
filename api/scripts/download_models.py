"""Unduh pretrained weights Real-ESRGAN ke `model_dir` (idempotent).

Dipakai saat build image worker GPU (Dockerfile.worker) agar model sudah
ter-bake ke image, dan bisa dijalankan manual:

    python scripts/download_models.py            # ke settings.model_dir
    python scripts/download_models.py --dir /path  # ke folder tertentu

Referensi weights: github.com/xinntao/Real-ESRGAN/releases (ADR-002).
"""

import argparse
import sys
import urllib.request
from pathlib import Path

# URL resmi dari rilis Real-ESRGAN (ADR-002: model utama x4plus).
MODELS = {
    "RealESRGAN_x4plus.pth": (
        "https://github.com/xinntao/Real-ESRGAN/releases/download/"
        "v0.1.0/RealESRGAN_x4plus.pth"
    ),
}
# File rusak/parsial biasanya < 1 MB; cukup cek ukuran untuk idempotent.
_MIN_VALID_BYTES = 1_000_000


def default_dir() -> Path:
    try:
        from app.core.config import settings
    except ImportError:
        return Path("storage/models")
    return Path(settings.model_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=None, help="Folder tujuan")
    args = parser.parse_args()
    model_dir = args.dir or default_dir()
    model_dir.mkdir(parents=True, exist_ok=True)

    failed = False
    for name, url in MODELS.items():
        dst = model_dir / name
        if dst.exists() and dst.stat().st_size >= _MIN_VALID_BYTES:
            print(f"SKIP  {name} (sudah ada, {dst.stat().st_size / 1e6:.1f} MB)")
            continue
        print(f"UNDUH {name} -> {dst} ...")
        try:
            urllib.request.urlretrieve(url, dst)
            size_mb = dst.stat().st_size / 1e6
            print(f"OK    {name} ({size_mb:.1f} MB)")
        except Exception as exc:  # noqa: BLE001 — laporkan lalu lanjut model lain
            failed = True
            print(f"GAGAL {name}: {exc}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
