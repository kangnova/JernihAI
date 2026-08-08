"""Generator set uji gambar sintetis untuk development JernihAI.

Stdlib-only (tidak butuh Pillow/OpenCV/ML) sehingga aman dijalankan di
laptop dev yang tidak mendukung AVX2 (lihat prd.md §12).

Usage:
    python scripts/make_test_images.py [output_dir]
"""

from __future__ import annotations

import pathlib
import random
import struct
import sys
import zlib


def _chunk(typ: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + typ
        + data
        + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
    )


def make_png(path: pathlib.Path, width: int, height: int, seed: int) -> None:
    """Buat PNG RGB 8-bit: gradien warna + noise ringan (simulasi foto noisey)."""
    rng = random.Random(seed)
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type: None
        for x in range(width):
            r = (x * 255 // max(width - 1, 1)) ^ rng.randint(0, 8)
            g = (y * 255 // max(height - 1, 1)) ^ rng.randint(0, 8)
            b = (255 - (x * 255 // max(width - 1, 1))) ^ rng.randint(0, 8)
            raw += bytes((r, g, b))

    png = b"\x89PNG\r\n\x1a\n"
    png += _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += _chunk(b"IEND", b"")
    path.write_bytes(png)


def main() -> None:
    out_dir = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "samples")
    out_dir.mkdir(parents=True, exist_ok=True)

    specs = [
        ("gradient_512.png", 512, 512, 1),
        ("noisy_256.png", 256, 256, 7),
        ("pattern_1024.png", 1024, 768, 13),
    ]
    for name, width, height, seed in specs:
        target = out_dir / name
        make_png(target, width, height, seed)
        print(f"ok  {target} ({width}x{height})")


if __name__ == "__main__":
    main()
