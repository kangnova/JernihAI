"""Penyimpanan file upload & hasil — Fase 1: disk lokal.

Abstraksi sengaja dibuat tipis agar mudah diganti ke Cloudflare R2 (Fase 2)
tanpa mengubah call site di routes. Semua path di DB disimpan RELATIF
terhadap root repo (mis. `storage/uploads/<id>.png`), sehingga portabel
antara dev lokal dan container worker (volume bersama `./api:/app`).
"""

from pathlib import Path

from app.core.config import settings

# Magic bytes per format — validasi konten, bukan ekstensi (prd.md FR-02).
_JPEG = b"\xff\xd8\xff"
_PNG = b"\x89PNG\r\n\x1a\n"
_WEBP_MAGIC = b"WEBP"


def detect_image_format(data: bytes) -> str | None:
    """Deteksi format gambar dari magic bytes; None bila bukan JPG/PNG/WebP."""
    if data.startswith(_JPEG):
        return "jpeg"
    if data.startswith(_PNG):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == _WEBP_MAGIC:
        return "webp"
    return None


def save_upload(data: bytes, job_id: str, ext: str) -> str:
    """Simpan upload ke `upload_dir`; return path relatif (disimpan di DB)."""
    rel = f"{settings.upload_dir}/{job_id}.{ext}"
    path = Path(rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return rel


def resolve(rel_path: str) -> Path:
    """Konversi path relatif dari DB menjadi Path absolut kerja."""
    return Path(rel_path)


def delete_if_inside(rel_path: str | None, base_dir: str) -> bool:
    """Hapus file bila berada di dalam base_dir (guard path traversal).

    Dipakai admin (hapus job) & retensi (FR-07) — file hanya boleh dihapus
    bila path-nya benar-benar di dalam `upload_dir`/`result_dir`, supaya
    path menyimpang dari DB tidak bisa menghapus file arbitrer.
    """
    if not rel_path:
        return False
    try:
        resolved = Path(rel_path).resolve()
        base = Path(base_dir).resolve()
        if resolved.is_relative_to(base) and resolved.is_file():
            resolved.unlink(missing_ok=True)
            return True
    except OSError:
        # File lock / akses ditolak — jangan crash endpoint, cukup False.
        return False
    return False
