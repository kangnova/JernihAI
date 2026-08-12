"""Tests storage abstraction (Fase 3) — backend lokal & Cloudflare R2.

Menguji:
- `save_upload`/`delete_if_inside` lokal: path traversal guard, upload & hapus.
- `publish_result` lokal: no-op (file sudah di posisi akhir).
- Backend R2 (boto3 di-mock): put/get/delete + presigned download URL,
  guard traversal berbasis prefiks key.
"""

import pathlib

import pytest

from app.core import storage as storage_module
from app.core.config import settings


@pytest.fixture(autouse=True)
def local_backend(monkeypatch, tmp_path):
    """Default: backend lokal + folder storage sementara + reset R2 client."""
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "result_dir", str(tmp_path / "results"))
    storage_module._r2_client = None
    yield
    storage_module._r2_client = None


async def test_save_upload_writes_to_disk(tmp_path):
    rel = await storage_module.save_upload(b"foto", job_id="j1", ext="png")
    assert rel == f"{settings.upload_dir}/j1.png"
    assert pathlib.Path(rel).read_bytes() == b"foto"


async def test_delete_if_inside_deletes_upload():
    rel = await storage_module.save_upload(b"x", job_id="j2", ext="png")
    assert await storage_module.delete_if_inside(rel, settings.upload_dir) is True
    assert not pathlib.Path(rel).exists()


async def test_delete_ignores_path_outside_base(tmp_path):
    """Guard traversal: file di luar base_dir tidak dihapus."""
    outside = tmp_path / "luar.png"
    outside.write_bytes(b"data")
    assert (
        await storage_module.delete_if_inside(str(outside), settings.upload_dir)
        is False
    )
    assert outside.exists()


async def test_delete_none_or_empty_is_false():
    assert await storage_module.delete_if_inside(None, settings.upload_dir) is False
    assert await storage_module.delete_if_inside("", settings.upload_dir) is False


async def test_publish_result_local_is_noop():
    """Backend lokal: hasil sudah di posisi akhir — file tidak hilang."""
    rel = f"{settings.result_dir}/j3.webp"
    pathlib.Path(rel).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(rel).write_bytes(b"hasil")
    await storage_module.publish_result(rel)
    assert pathlib.Path(rel).exists()


async def test_download_url_local_is_none():
    assert await storage_module.download_url("storage/results/j4.webp", "j4.webp") is None


# --- Backend R2 (boto3 di-mock) ---


class _FakeS3:
    """Fake boto3 client — mencatat operasi, menyimpan objek di memori."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.uploaded: list[tuple[str, bytes]] = []

    def put_object(self, Bucket, Key, Body):
        self.objects[Key] = Body
        self.uploaded.append((Key, Body))

    def get_object(self, Bucket, Key):
        return {"Body": _FakeBody(self.objects[Key])}

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)
        self.deleted.append(Key)

    def generate_presigned_url(self, ClientMethod, Params, ExpiresIn):
        return f"https://presigned.example/{Params['Key']}?x-amz-signature=fake"


class _FakeBody:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data

    def close(self):
        return None


@pytest.fixture()
def fake_s3(monkeypatch):
    client = _FakeS3()
    monkeypatch.setattr(settings, "storage_backend", "r2")
    monkeypatch.setattr(settings, "r2_bucket", "jernihai-test")
    monkeypatch.setattr(storage_module, "_get_r2_client", lambda: client)
    return client


async def test_r2_save_upload_puts_object(fake_s3):
    rel = await storage_module.save_upload(b"foto-r2", job_id="r1", ext="png")
    assert rel == f"{settings.upload_dir}/r1.png"
    assert fake_s3.objects[rel] == b"foto-r2"
    # Tidak menulis ke disk lokal.
    assert not pathlib.Path(rel).exists()


async def test_r2_publish_result_uploads_and_removes_local(fake_s3):
    rel = f"{settings.result_dir}/r2.webp"
    pathlib.Path(rel).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(rel).write_bytes(b"hasil-r2")

    await storage_module.publish_result(rel)

    assert fake_s3.objects[rel] == b"hasil-r2"
    assert not pathlib.Path(rel).exists()  # salinan lokal dibersihkan


async def test_r2_ensure_local_downloads(fake_s3):
    rel = f"{settings.upload_dir}/r3.png"
    fake_s3.objects[rel] = b"original-r2"

    await storage_module.ensure_local(rel)

    assert pathlib.Path(rel).read_bytes() == b"original-r2"


async def test_r2_ensure_local_skips_when_already_local(fake_s3):
    """File lokal sudah ada → tidak men-download ulang (hemat bandwidth)."""
    rel = f"{settings.upload_dir}/r3b.png"
    pathlib.Path(rel).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(rel).write_bytes(b"sudah-ada")
    fake_s3.objects[rel] = b"versi-bucket"

    await storage_module.ensure_local(rel)

    # Isi lokal dipertahankan (tidak ditimpa bucket).
    assert pathlib.Path(rel).read_bytes() == b"sudah-ada"


async def test_cleanup_local_noop_for_local_backend():
    """Backend lokal: cleanup TIDAK menghapus apa pun (file = storage asli)."""
    rel = f"{settings.upload_dir}/keep.png"
    pathlib.Path(rel).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(rel).write_bytes(b"asli")

    await storage_module.cleanup_local(rel)

    assert pathlib.Path(rel).exists()


async def test_cleanup_local_removes_only_r2_copies(fake_s3):
    """R2: cleanup menghapus SALINAN lokal, tidak menyentuh object bucket."""
    rel = f"{settings.upload_dir}/tmp.png"
    pathlib.Path(rel).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(rel).write_bytes(b"salinan")
    fake_s3.objects[rel] = b"asli-di-bucket"

    await storage_module.cleanup_local(rel)

    assert not pathlib.Path(rel).exists()  # salinan lokal bersih
    assert fake_s3.objects[rel] == b"asli-di-bucket"  # bucket aman


async def test_cleanup_local_ignores_none():
    await storage_module.cleanup_local(None, "")
    await storage_module.cleanup_local()


async def test_r2_delete_removes_object(fake_s3):
    rel = f"{settings.upload_dir}/r4.png"
    fake_s3.objects[rel] = b"x"

    assert await storage_module.delete_if_inside(rel, settings.upload_dir) is True
    assert rel in fake_s3.deleted
    assert rel not in fake_s3.objects


async def test_r2_delete_rejects_key_outside_base(fake_s3):
    """Guard traversal R2: key di luar prefiks base_dir ditolak (tetap 1 cek)."""
    evil = "../uploads/evils.png"
    assert await storage_module.delete_if_inside(evil, settings.upload_dir) is False
    assert fake_s3.deleted == []


async def test_r2_download_url_presigned(fake_s3):
    url = await storage_module.download_url(
        f"{settings.result_dir}/r5.webp", "r5-2x.webp"
    )
    assert url and url.startswith("https://presigned.example/")
