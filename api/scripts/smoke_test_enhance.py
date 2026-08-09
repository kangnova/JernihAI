"""Smoke test pipeline Real-ESRGAN asli — dijalankan di GPU box / Colab.

Mengukur komponen yang sama dengan produksi (`app.tasks.enhance`):
waktu load model, inference (cold + warm), dan encode ADR-004. TIDAK
membutuhkan Redis/Postgres — murni tes pipeline ML.

Cara pakai (di dalam worker image / instance GPU):
    python scripts/smoke_test_enhance.py <gambar> --scale 4
    python scripts/smoke_test_enhance.py --gen-1080p --scale 4   # KPI NFR-01

Exit code 0 = sukses, 1 = backend real tidak tersedia / gagal.
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

from app.core.config import settings

# Memakai loader & encoder produksi agar yang diukur = kode asli (bukan
# duplikasi). Import `app.tasks.enhance` aman tanpa broker/DB aktif.
from app.tasks.enhance import _encode_and_save, _get_upsampler


def _synthetic_job(scale: int, output_format: str, name: str):
    from app.models.job import Job  # instansiasi ringan, tanpa DB

    return Job(
        id="smoke-test",
        user_id="smoke",
        status="completed",
        scale=scale,
        output_format=output_format,
        original_name=name,
        original_path=name,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", help="Path gambar uji")
    parser.add_argument("--scale", type=int, default=4, choices=[2, 4])
    parser.add_argument("--format", default="webp", choices=["webp", "jpeg", "png"])
    parser.add_argument("--out-dir", default="/tmp/smoke", help="Folder output")
    parser.add_argument("--iters", type=int, default=3, help="Jumlah pengukuran")
    parser.add_argument(
        "--gen-1080p",
        action="store_true",
        help="Buat gambar uji 1920x1080 bila --input tidak diberikan (KPI NFR-01)",
    )
    args = parser.parse_args()
    if args.iters < 1:
        parser.error("--iters harus >= 1")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # Semua hasil (input generate + output encode) jatuh ke --out-dir.
    settings.result_dir = str(out_dir)

    # --- 1) Load model (sekali, seperti worker) ---
    print("== 1) Memuat model Real-ESRGAN (sekali per proses) ==")
    t0 = time.perf_counter()
    upsampler = _get_upsampler()
    load_s = time.perf_counter() - t0
    if upsampler is None:
        print("GAGAL: backend real tidak tersedia — cek torch/weights.", file=sys.stderr)
        return 1

    import torch

    device = next(upsampler.model.parameters()).device
    print(f"   device        : {device}")
    print(f"   torch         : {torch.__version__}")
    if device.type == "cuda":
        props = torch.cuda.get_device_properties(0)
        print(f"   GPU           : {props.name} ({props.total_memory / 1e9:.1f} GB)")
        free, _ = torch.cuda.mem_get_info()
        print(f"   VRAM bebas    : {free / 1e9:.1f} GB")
    # realesrgan 0.3.0 menamai atribut tile sebagai `tile_size`
    print(f"   tile          : {upsampler.tile_size} (tile_pad={upsampler.tile_pad})")
    print(f"   half (FP16)   : {upsampler.half}")
    print(f"   load model    : {load_s:.2f} s")

    # --- 2) Siapkan input ---
    if args.input:
        src = Path(args.input)
        if not src.exists():
            print(f"GAGAL: file tidak ada: {src}", file=sys.stderr)
            return 1
    elif args.gen_1080p:
        src = out_dir / "test_1080p.png"
        w, h = 1920, 1080
        rng = np.random.default_rng(42)
        # Gradien + noise ringan = representatif foto realistis.
        base = np.linspace(60, 200, h, dtype=np.uint8)[:, None]
        img = np.repeat(base, w, axis=1)
        img = np.stack([img, img[::-1], img], axis=2)
        img = np.clip(img.astype(np.int16) + rng.integers(-8, 8, img.shape), 0, 255)
        Image.fromarray(img.astype(np.uint8), "RGB").save(src)
        print(f"   input         : dibuat {src} ({w}x{h})")
    else:
        print("GAGAL: berikan --input atau --gen-1080p", file=sys.stderr)
        return 1

    with Image.open(src) as opened:
        img = opened.convert("RGBA" if "A" in opened.getbands() else "RGB")
    arr = np.array(img)
    print(f"   input         : {src.name} {img.size[0]}x{img.size[1]} mode={img.mode}")

    # --- 3) Warmup + pengukuran ---
    print(f"== 2) Inference outscale={args.scale} ({args.iters}x, pertama = cold) ==")
    times: list[float] = []
    for i in range(args.iters):
        t0 = time.perf_counter()
        output, _ = upsampler.enhance(arr, outscale=args.scale)
        times.append(time.perf_counter() - t0)
        tag = "cold (termasuk alokasi CUDA)" if i == 0 else ""
        print(f"   iter {i + 1}: {times[-1]:.2f} s {tag}")
    out_img = Image.fromarray(output)
    # Rata-rata tanpa iter cold; dengan iters=1 tidak ada pengukuran warm.
    mean = sum(times[1:]) / (len(times) - 1) if len(times) >= 2 else None

    # --- 4) Encode (ADR-004) ---
    print(f"== 3) Encode {args.format} ==")
    t0 = time.perf_counter()
    job = _synthetic_job(args.scale, args.format, src.name)
    rel = _encode_and_save(out_img, job)
    enc_s = time.perf_counter() - t0
    dst = Path(rel)
    print(f"   output        : {out_img.size[0]}x{out_img.size[1]} mode={out_img.mode}")
    out_size_mb = dst.stat().st_size / 1e6
    print(f"   encode        : {enc_s:.2f} s -> {dst.resolve()} ({out_size_mb:.2f} MB)")

    # --- 5) Ringkasan ---
    print("\n== RINGKASAN ==")
    print(f"   load model    : {load_s:.2f} s")
    print(f"   inference cold: {times[0]:.2f} s")
    warm_txt = f"{mean:.2f} s (rata2 iter 2..n)" if mean is not None else "n/a (butuh --iters >= 2)"
    print(f"   inference warm: {warm_txt}")
    print(f"   total pipeline: {load_s + times[0] + enc_s:.2f} s (cold, 1 gambar)")
    if out_img.size[0] >= 1920 and mean is not None:
        verdict = "OK" if mean < 15 else "BELUM"
        print(f"   KPI NFR-01     : target <15 s utk 1080p warm -> {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
