"""Penyimpanan file upload & hasil — backend: disk lokal (dev/test) atau
Cloudflare R2 (produksi, FR-07 & multi-node).

Semua path di DB disimpan RELATIF terhadap root repo (mis.
`storage/uploads/<id>.png`), sehingga portabel antara dev lokal, container
worker, dan key R2 (key = path relatif, struktur bucket meniru folder lokal).

Pola R2 (Fase 3):
- Upload: bytes dikirim langsung ke bucket (tidak lewat disk api).
- Download: endpoint menjawab 302 ke **presigned URL** (egress R2 gratis;
  lihat `download_url`). Fallback ke FileResponse saat backend lokal.
- Pipeline worker tetap menulis ke disk lokal sementara (lihat
  `ensure_local` / `publish_result`) — abstraksi ini yang memindahkan file
  ke/ dari R2, call site pipeline tidak berubah.
- Penghapusan (retensi FR-07 / admin / hapus akun) memakai `delete_if_inside`
  yang bekerja untuk kedua backend + guard path traversal.

Boto3 di-import LAZY: hanya dipakai saat backend=r2, agar image worker
dev (tanpa boto3) tetap bisa jalan.
"""

import asyncio
import threading
from contextlib import suppress
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


# --- Backend helpers ---


def _is_r2() -> bool:
    return settings.storage_backend == "r2"


def _local_path(rel_path: str) -> Path:
    """Path disk lokal untuk path relatif (CWD = root repo / container /app)."""
    return Path(rel_path)


_r2_client = None
_r2_client_lock = threading.Lock()


def _get_r2_client():
    """boto3 S3 client untuk R2 — lazy, dibuat sekali per proses.

    Double-checked locking: FastAPI melayani banyak request bersamaan;
    tanpa lock beberapa client bisa dibuat (boros, walau sama-sama jalan).
    """
    global _r2_client
    if _r2_client is None:
        with _r2_client_lock:
            if _r2_client is None:
                import boto3
                from botocore.config import Config

                _r2_client = boto3.client(
                    "s3",
                    endpoint_url=(
                        f"https://{settings.r2_account_id}.r2.cloudflarestorage.com"
                    ),
                    aws_access_key_id=settings.r2_access_key_id,
                    aws_secret_access_key=settings.r2_secret_access_key,
                    region_name="auto",
                    config=Config(signature_version="s3v4"),
                )
    return _r2_client


async def _r2_put(key: str, data: bytes) -> None:
    client = _get_r2_client()
    await asyncio.to_thread(
        client.put_object, Bucket=settings.r2_bucket, Key=key, Body=data
    )


async def _r2_get(key: str) -> bytes:
    client = _get_r2_client()

    def _read():
        resp = client.get_object(Bucket=settings.r2_bucket, Key=key)
        body = resp["Body"]
        try:
            return body.read()
        finally:
            body.close()

    return await asyncio.to_thread(_read)


async def _r2_delete(key: str) -> bool:
    await asyncio.to_thread(
        _get_r2_client().delete_object, Bucket=settings.r2_bucket, Key=key
    )
    return True


# --- Operasi utama (async) ---


async def save_upload(data: bytes, job_id: str, ext: str) -> str:
    """Simpan upload; return path relatif (disimpan di DB).

    Local: tulis ke disk. R2: upload ke bucket (key = path relatif).
    """
    rel = f"{settings.upload_dir}/{job_id}.{ext}"
    if _is_r2():
        await _r2_put(rel, data)
    else:
        path = _local_path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return rel


async def ensure_local(rel_path: str) -> None:
    """Pastikan file tersedia di disk lokal (R2: download bila belum ada).

    Dipanggil pipeline sebelum memproses — pipeline tetap memakai
    `Image.open(path)` seperti biasa; untuk backend local ini no-op.
    """
    if not _is_r2():
        return
    path = _local_path(rel_path)
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(await _r2_get(rel_path))


async def publish_result(rel_path: str) -> None:
    """Kirim hasil proses ke penyimpanan final.

    R2: upload dari disk lokal ke bucket, lalu hapus salinan lokal.
    Local: file sudah di posisi akhir (no-op).
    """
    if not _is_r2():
        return
    path = _local_path(rel_path)
    await _r2_put(rel_path, path.read_bytes())
    path.unlink(missing_ok=True)


async def cleanup_local(*rel_paths: str | None) -> None:
    """Hapus SALINAN lokal sementara (hanya saat backend R2).

    Dipanggil pipeline dalam `finally` — file yang di-`ensure_local`
    (download original) atau hasil yang gagal ditulis TIDAK boleh
    menumpuk di disk worker. Untuk backend local ini no-op: file lokal
    ADALAH penyimpanan asli (jangan pernah dihapus di sini).
    """
    if not _is_r2():
        return
    for rel in rel_paths:
        if not rel:
            continue
        with suppress(OSError):
            _local_path(rel).unlink(missing_ok=True)


async def delete_if_inside(rel_path: str | None, base_dir: str) -> bool:
    """Hapus file bila berada di dalam base_dir (guard path traversal).

    Bekerja untuk kedua backend. Dipakai admin (hapus job), retensi (FR-07),
    hapus akun & rollback upload — file hanya boleh dihapus bila path-nya
    benar-benar di dalam `upload_dir`/`result_dir`, supaya path menyimpang
    dari DB tidak bisa menghapus file arbitrer.
    """
    if not rel_path:
        return False
    if _is_r2():
        # Guard berbasis prefiks key (path relatif = key R2).
        if not _rel_inside(rel_path, base_dir):
            return False
        return await _r2_delete(rel_path)
    try:
        resolved = _local_path(rel_path).resolve()
        base = _local_path(base_dir).resolve()
        if not resolved.is_relative_to(base):
            return False
    except OSError:
        return False
    try:
        if resolved.is_file():
            resolved.unlink(missing_ok=True)
            return True
    except OSError:
        return False
    return False


def _rel_inside(rel_path: str, base_dir: str) -> bool:
    """Guard untuk R2: key harus berada dalam prefiks base_dir (relatif)."""
    return rel_path.startswith(base_dir.rstrip("/") + "/") or rel_path == base_dir


def resolve(rel_path: str) -> Path:
    """Konversi path relatif dari DB menjadi Path lokal kerja.

    Dipakai endpoint download (fallback FileResponse saat backend lokal).
    """
    return _local_path(rel_path)


async def download_url(rel_path: str, filename: str) -> str | None:
    """URL unduhan publik.

    R2: presigned URL (expire 1 jam) — egress gratis, tanpa melalui api.
    Local: None → caller memakai FileResponse.
    """
    if not _is_r2():
        return None
    client = _get_r2_client()
    return await asyncio.to_thread(
        client.generate_presigned_url,
        "get_object",
        Params={
            "Bucket": settings.r2_bucket,
            "Key": rel_path,
            "ResponseContentDisposition": f'attachment; filename="{filename}"',
        },
        ExpiresIn=3600,
    )
