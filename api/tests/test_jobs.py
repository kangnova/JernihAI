"""Tests jobs — upload, validasi konten (magic bytes), status, download (Fase 1).

Pipeline dijalankan inline (mode eager) sehingga end-to-end
(register → upload → job selesai → download) diuji tanpa Redis/worker.
"""

import io
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.tasks.enhance as enhance_module
from app.core.config import settings
from app.db.session import get_db
from app.main import app
from app.models.base import Base
from app.models.job import Job, JobStatus


def _image_bytes(fmt: str) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 100, 50)).save(buf, format=fmt.upper())
    return buf.getvalue()


@pytest.fixture()
async def db(tmp_path, monkeypatch):
    """SQLite in-memory (StaticPool) + pipeline eager + storage sementara."""
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(settings, "celery_task_always_eager", True)
    monkeypatch.setattr(settings, "enhance_backend", "mock")  # Fase 2: tanpa ML lokal
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "result_dir", str(tmp_path / "results"))
    # Sesi yang dipakai task = sesi yang sama dengan route (DB test).
    monkeypatch.setattr(enhance_module, "async_session_factory", factory)

    yield factory

    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.fixture()
async def client(db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _register(client, email: str = "user@example.com") -> str:
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "password123",
            "name": "Tono",
            "privacy_consent": True,
        },
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def _upload(
    client, fmt: str = "png", *, scale: str = "2", output_format: str = "webp"
) -> dict:
    resp = await client.post(
        "/api/v1/jobs",
        files={"file": (f"foto.{fmt}", _image_bytes(fmt), f"image/{fmt}")},
        data={"scale": scale, "output_format": output_format},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_upload_requires_auth(client):
    resp = await client.post(
        "/api/v1/jobs",
        files={"file": ("a.png", _image_bytes("png"), "image/png")},
        data={"scale": "2"},
    )
    assert resp.status_code == 401


async def test_upload_png_creates_completed_job(client):
    await _register(client)
    data = await _upload(client, "png")
    assert data["status"] == JobStatus.COMPLETED.value
    assert data["scale"] == 2
    assert data["output_format"] == "webp"
    assert data["original_name"] == "foto.png"
    assert data["finished_at"] is not None
    assert data["error"] is None


async def test_upload_accepts_jpeg_and_webp(client):
    await _register(client)
    for fmt in ("jpeg", "webp"):
        data = await _upload(client, fmt)
        assert data["status"] == JobStatus.COMPLETED.value, fmt


async def test_upload_respects_scale_and_format(client):
    await _register(client)
    data = await _upload(client, "png", scale="4", output_format="jpeg")
    assert data["scale"] == 4
    assert data["output_format"] == "jpeg"


async def test_upload_face_enhance_default_off(client):
    """FR-08: face_enhance default False di respons JobOut."""
    await _register(client)
    data = await _upload(client, "png")
    assert data["face_enhance"] is False


async def test_upload_face_enhance_on(client):
    """FR-08: face_enhance=True diterima & dipertahankan (mock tetap sukses)."""
    await _register(client)
    resp = await client.post(
        "/api/v1/jobs",
        files={"file": ("foto.png", _image_bytes("png"), "image/png")},
        data={"scale": "2", "output_format": "webp", "face_enhance": "true"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["status"] == JobStatus.COMPLETED.value
    assert data["face_enhance"] is True


async def test_upload_denoise_color_default_off(client):
    """FR-09: denoise & color_enhance default False di respons JobOut."""
    await _register(client)
    data = await _upload(client, "png")
    assert data["denoise"] is False
    assert data["color_enhance"] is False


async def test_upload_denoise_color_on(client):
    """FR-09: toggle denoise & color_enhance diterima (mock tetap sukses)."""
    await _register(client)
    resp = await client.post(
        "/api/v1/jobs",
        files={"file": ("foto.png", _image_bytes("png"), "image/png")},
        data={
            "scale": "2",
            "output_format": "webp",
            "denoise": "true",
            "color_enhance": "true",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["status"] == JobStatus.COMPLETED.value
    assert data["denoise"] is True
    assert data["color_enhance"] is True


async def test_upload_rejects_non_image_content(client):
    await _register(client)
    resp = await client.post(
        "/api/v1/jobs",
        files={"file": ("fake.png", b"ini bukan gambar", "image/png")},
        data={"scale": "2"},
    )
    assert resp.status_code == 415


async def test_upload_rejects_oversize(client, monkeypatch):
    await _register(client)
    monkeypatch.setattr(settings, "max_upload_bytes", 100)
    resp = await client.post(
        "/api/v1/jobs",
        files={"file": ("big.png", b"\x89PNG\r\n\x1a\n" + b"0" * 200, "image/png")},
        data={"scale": "2"},
    )
    assert resp.status_code == 413


async def test_upload_rejects_invalid_scale(client):
    await _register(client)
    resp = await client.post(
        "/api/v1/jobs",
        files={"file": ("a.png", _image_bytes("png"), "image/png")},
        data={"scale": "3"},
    )
    assert resp.status_code == 400


async def test_upload_rejects_invalid_output_format(client):
    await _register(client)
    resp = await client.post(
        "/api/v1/jobs",
        files={"file": ("a.png", _image_bytes("png"), "image/png")},
        data={"scale": "2", "output_format": "gif"},
    )
    assert resp.status_code == 400


async def test_list_jobs_requires_auth(client):
    resp = await client.get("/api/v1/jobs")
    assert resp.status_code == 401


async def test_list_jobs_empty(client):
    await _register(client)
    resp = await client.get("/api/v1/jobs")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"items": [], "total": 0}


async def test_list_jobs_returns_own_jobs_newest_first(client):
    """FR-10: hanya job milik user + urutan terbaru dulu."""
    await _register(client)
    for _ in range(3):
        await _upload(client, "png")
    resp = await client.get("/api/v1/jobs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    items = body["items"]
    assert len(items) == 3
    # Terbaru dulu (created_at descending).
    created = [i["created_at"] for i in items]
    assert created == sorted(created, reverse=True)
    # Item memuat field yang dibutuhkan UI riwayat.
    assert all(i["original_name"] == "foto.png" for i in items)
    assert all(i["face_enhance"] is False for i in items)
    assert all(i["result_deleted_at"] is None for i in items)
    assert all("user_id" not in i for i in items)


async def test_list_jobs_does_not_leak_other_users_jobs(client):
    """FR-10: job user lain tidak bocor ke riwayat user ini."""
    await _register(client, "owner@example.com")
    await _upload(client, "png")
    await _register(client, "other@example.com")  # cookie berganti user
    resp = await client.get("/api/v1/jobs")
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0}


async def test_list_jobs_pagination(client, monkeypatch):
    """FR-10: limit/offset bekerja (halaman 1 vs 2)."""
    # FR-06 default 3/hari — naikkan agar 5 upload diterima.
    monkeypatch.setattr(settings, "free_daily_quota", 10)
    await _register(client)
    for _ in range(5):
        await _upload(client, "png")
    page1 = (await client.get("/api/v1/jobs?limit=2&offset=0")).json()
    page2 = (await client.get("/api/v1/jobs?limit=2&offset=2")).json()
    assert page1["total"] == 5
    assert len(page1["items"]) == 2
    assert len(page2["items"]) == 2
    # Tidak ada duplikat antar halaman.
    ids1 = {i["id"] for i in page1["items"]}
    ids2 = {i["id"] for i in page2["items"]}
    assert ids1.isdisjoint(ids2)


async def test_list_jobs_rejects_invalid_pagination(client):
    await _register(client)
    resp = await client.get("/api/v1/jobs?limit=0")
    assert resp.status_code == 422
    resp = await client.get("/api/v1/jobs?limit=1000")
    assert resp.status_code == 422


async def test_get_job_status_ok(client):
    await _register(client)
    job = await _upload(client, "png")
    resp = await client.get(f"/api/v1/jobs/{job['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == job["id"]
    assert body["status"] == JobStatus.COMPLETED.value
    assert body["created_at"]
    assert "user_id" not in body  # JobOut tidak memuat user_id


async def test_get_job_other_user_returns_404(client):
    await _register(client, "owner@example.com")
    job = await _upload(client, "png")
    await _register(client, "other@example.com")  # cookie berganti ke user lain
    resp = await client.get(f"/api/v1/jobs/{job['id']}")
    assert resp.status_code == 404


async def test_download_result_ok(client):
    await _register(client)
    job = await _upload(client, "png")
    resp = await client.get(f"/api/v1/jobs/{job['id']}/download")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/webp"
    assert resp.content[:4] == b"RIFF"  # magic bytes WebP
    assert f"{job['id']}-2x.webp" in resp.headers.get("content-disposition", "")


async def test_download_queued_conflict(client, db):
    user_id = await _register(client)
    async with db() as session:
        session.add(
            Job(
                id="manual-queued",
                user_id=user_id,
                status=JobStatus.QUEUED.value,
                scale=2,
                output_format="webp",
                original_name="x.png",
                original_path="storage/uploads/x.png",
            )
        )
        await session.commit()
    resp = await client.get("/api/v1/jobs/manual-queued/download")
    assert resp.status_code == 409


async def test_download_missing_file_404(client, db):
    user_id = await _register(client)
    async with db() as session:
        session.add(
            Job(
                id="manual-completed",
                user_id=user_id,
                status=JobStatus.COMPLETED.value,
                scale=2,
                output_format="webp",
                original_name="x.png",
                original_path="storage/uploads/x.png",
                result_path=f"{settings.result_dir}/ghost.webp",
            )
        )
        await session.commit()
    resp = await client.get("/api/v1/jobs/manual-completed/download")
    assert resp.status_code == 404


async def test_failed_job_when_original_missing(client, db):
    """Jalur FAILED: original hilang -> job failed + error terisi (state machine)."""
    user_id = await _register(client)
    async with db() as session:
        session.add(
            Job(
                id="manual-failed",
                user_id=user_id,
                status=JobStatus.QUEUED.value,
                scale=2,
                output_format="webp",
                original_name="x.png",
                original_path=f"{settings.upload_dir}/ghost.png",
            )
        )
        await session.commit()

    status = await enhance_module.process_job("manual-failed")
    assert status == JobStatus.FAILED.value

    async with db() as session:
        job = await session.get(Job, "manual-failed")
        assert job is not None
        assert job.status == JobStatus.FAILED.value
        assert "ghost.png" in (job.error or "")
        assert job.finished_at is None


# --- Fase 2: backend enhance (Real-ESRGAN vs mock) ---


def test_effective_outscale_capped_to_max_output(monkeypatch, tmp_path):
    """Scale efektif dibatasi agar output <= max_output_longest (ADR-004)."""
    monkeypatch.setattr(settings, "max_output_longest", 200)
    src = tmp_path / "big.png"
    Image.new("RGB", (64, 64), "red").save(src)
    job = Job(
        id="cap-test",
        user_id="u",
        status=JobStatus.QUEUED.value,
        scale=4,
        output_format="webp",
        original_name="big.png",
        original_path=str(src),
    )
    # 64 * 4 = 256 > 200 -> clamp ke 200 // 64 = 3 (output 192px)
    assert enhance_module._effective_outscale(job) == 3


def test_effective_outscale_unaffected_within_limit(monkeypatch, tmp_path):
    src = tmp_path / "small.png"
    Image.new("RGB", (64, 64)).save(src)
    job = Job(
        id="no-cap",
        user_id="u",
        status=JobStatus.QUEUED.value,
        scale=4,
        output_format="webp",
        original_name="small.png",
        original_path=str(src),
    )
    assert enhance_module._effective_outscale(job) == 4


async def test_backend_real_fails_loudly_when_model_missing(client, db, monkeypatch):
    """backend=real tanpa model -> job FAILED (bukan mock senyap)."""
    monkeypatch.setattr(settings, "enhance_backend", "real")
    monkeypatch.setattr(enhance_module, "_get_upsampler", lambda denoise=False: None)
    monkeypatch.setattr(
        enhance_module, "_upsampler_error", "simulasi: torch/torch tidak tersedia"
    )

    user_id = await _register(client)
    src = Path(settings.upload_dir) / "valid.png"
    src.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64)).save(src)
    async with db() as session:
        session.add(
            Job(
                id="real-fail",
                user_id=user_id,
                status=JobStatus.QUEUED.value,
                scale=2,
                output_format="webp",
                original_name="valid.png",
                original_path=str(src),
            )
        )
        await session.commit()

    status = await enhance_module.process_job("real-fail")
    assert status == JobStatus.FAILED.value
    async with db() as session:
        job = await session.get(Job, "real-fail")
        assert "simulasi" in (job.error or "")


async def test_backend_auto_falls_back_to_mock(client, db, monkeypatch):
    """backend=auto tanpa model -> job sukses via mock (prd.md §12)."""
    monkeypatch.setattr(settings, "enhance_backend", "auto")
    monkeypatch.setattr(enhance_module, "_get_upsampler", lambda denoise=False: None)

    await _register(client)
    data = await _upload(client, "png")
    assert data["status"] == JobStatus.COMPLETED.value
    assert data["error"] is None


async def test_backend_unknown_value_fails_loudly(client, db, monkeypatch):
    """ENHANCE_BACKEND tidak dikenal -> job failed (bukan silent mock)."""
    monkeypatch.setattr(settings, "enhance_backend", "garbage")

    await _register(client)
    data = await _upload(client, "png")
    assert data["status"] == JobStatus.FAILED.value
    assert "enhance_backend" in (data["error"] or "")


class _FakeNumpyArray(bytes):
    """Tiruan array numpy HWC uint8 — venv dev tidak punya numpy (extra `gpu`).

    Subclass `bytes` karena Pillow modern butuh objek bytes-like untuk
    `Image.fromarray` (bukan sekadar `__array_interface__`). Mendukung
    pola yang dipakai pipeline real: `asarray(img)[:, :, ::-1]` (tukar
    kanal RGB<->BGR) dan `.copy()`.
    """

    def __new__(cls, data: bytes, shape: tuple[int, int, int]):
        obj = super().__new__(cls, data)
        obj.shape = shape
        return obj

    def __getitem__(self, key):
        # Dukungan minimal `arr[:, :, ::-1]`: balik urutan kanal tiap pixel.
        if (
            isinstance(key, tuple)
            and len(key) == 3
            and key[0] == slice(None)
            and key[1] == slice(None)
            and key[2] == slice(None, None, -1)
        ):
            c = self.shape[2]
            data = bytearray(self)
            for i in range(0, len(data), c):
                data[i : i + c] = data[i : i + c][::-1]
            return _FakeNumpyArray(bytes(data), self.shape)
        return super().__getitem__(key)

    def copy(self):
        return self

    @property
    def __array_interface__(self):
        return {
            "shape": self.shape,
            "typestr": "|u1",
            "data": bytes(self),
            "version": 3,
        }


class _FakeNumpy:
    """Tiruan numpy minimal: np.asarray(Pillow) -> _FakeNumpyArray."""

    @staticmethod
    def asarray(img):
        w, h = img.size
        return _FakeNumpyArray(img.convert("RGB").tobytes(), (h, w, 3))


async def test_real_backend_face_enhance_uses_gfpganer(client, db, monkeypatch):
    """FR-08 (perbaikan v0.3.0): face_enhance memakai GFPGANer terpisah.

    realesrgan 0.3.0 TIDAK punya param `face_enhance` di
    `RealESRGANer.enhance()` — restorasi wajah via `GFPGANer` dengan
    `bg_upsampler` (pola inference_realesrgan.py v0.3.0).
    """
    gfpgan_calls: list[dict] = []
    upsampler_enhance_calls: list = []
    requested_modes: list[bool] = []

    class _FakeFaceEnhancer:
        def enhance(self, img, has_aligned=False, only_center_face=False, paste_back=True):
            gfpgan_calls.append(
                {
                    "has_aligned": has_aligned,
                    "only_center_face": only_center_face,
                    "paste_back": paste_back,
                }
            )
            return None, None, img

    class _FakeUpsampler:
        device = "cpu"

        def enhance(self, img, outscale=None):
            upsampler_enhance_calls.append(outscale)
            return img, None

    def fake_get_upsampler(denoise: bool = False):
        requested_modes.append(denoise)
        return _FakeUpsampler()

    monkeypatch.setitem(sys.modules, "numpy", _FakeNumpy())
    monkeypatch.setattr(settings, "enhance_backend", "real")
    monkeypatch.setattr(enhance_module, "_get_upsampler", fake_get_upsampler)
    monkeypatch.setattr(
        enhance_module,
        "_get_face_enhancer",
        lambda upsampler, outscale: _FakeFaceEnhancer(),
    )
    monkeypatch.setattr(enhance_module, "_upsampler_error", None)

    user_id = await _register(client)
    src = Path(settings.upload_dir) / "wajah.png"
    src.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), (120, 80, 40)).save(src)

    async with db() as session:
        session.add(
            Job(
                id="face-gfpgan",
                user_id=user_id,
                status=JobStatus.QUEUED.value,
                scale=2,
                output_format="webp",
                face_enhance=True,
                original_name="wajah.png",
                original_path=str(src),
            )
        )
        await session.commit()

    status = await enhance_module.process_job("face-gfpgan")

    assert status == JobStatus.COMPLETED.value
    # Up-sampling wajah via GFPGANer, bukan upsampler.enhance.
    assert len(gfpgan_calls) == 1
    assert gfpgan_calls[0] == {
        "has_aligned": False,
        "only_center_face": False,
        "paste_back": True,
    }
    assert upsampler_enhance_calls == []
    assert requested_modes == [False]  # mode default x4plus (bukan denoise)


async def test_real_backend_denoise_and_color_flags(client, db, monkeypatch):
    """FR-09: denoise -> mode general (DNI); color_enhance -> pra-pemrosesan
    warna (bytes BGR yang masuk RealESRGANer ≠ input asli)."""
    captured: dict = {}
    requested_modes: list[bool] = []

    class _FakeUpsampler:
        device = "cpu"

        def enhance(self, img, outscale=None):
            captured["outscale"] = outscale
            captured["bgr_bytes"] = bytes(img)  # data BGR yang masuk model
            return img, None

    def fake_get_upsampler(denoise: bool = False):
        requested_modes.append(denoise)
        return _FakeUpsampler()

    monkeypatch.setitem(sys.modules, "numpy", _FakeNumpy())
    monkeypatch.setattr(settings, "enhance_backend", "real")
    monkeypatch.setattr(enhance_module, "_get_upsampler", fake_get_upsampler)
    monkeypatch.setattr(enhance_module, "_upsampler_error", None)

    user_id = await _register(client)
    src = Path(settings.upload_dir) / "noisy.png"
    src.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), (200, 100, 50)).save(src)

    async with db() as session:
        session.add(
            Job(
                id="denoise-color",
                user_id=user_id,
                status=JobStatus.QUEUED.value,
                scale=2,
                output_format="webp",
                denoise=True,
                color_enhance=True,
                original_name="noisy.png",
                original_path=str(src),
            )
        )
        await session.commit()

    status = await enhance_module.process_job("denoise-color")

    assert status == JobStatus.COMPLETED.value
    assert requested_modes == [True]  # mode general (DNI) untuk denoise
    assert captured["outscale"] == 2
    # color enhance mengubah pixel sebelum konversi BGR. Bandingkan dengan
    # baseline TANPA color enhance (input asli (200,100,50) yang kanalnya
    # sudah dibalik ke BGR) — harus berbeda setelah pra-pemrosesan.
    raw = bytearray(Image.new("RGB", (32, 32), (200, 100, 50)).tobytes())
    for i in range(0, len(raw), 3):
        raw[i : i + 3] = raw[i : i + 3][::-1]
    assert captured["bgr_bytes"] != bytes(raw)


def test_encode_and_save_rgba_to_jpeg_converts_rgb(monkeypatch, tmp_path):
    """Jalur real: output RGBA + format jpeg tidak boleh crash (P1 review)."""
    monkeypatch.setattr(settings, "result_dir", str(tmp_path / "results"))
    img = Image.new("RGBA", (16, 16), (10, 20, 30, 128))
    job = Job(
        id="rgba-jpeg",
        user_id="u",
        status=JobStatus.COMPLETED.value,
        scale=2,
        output_format="jpeg",
        original_name="x.png",
        original_path="x.png",
    )
    rel = enhance_module._encode_and_save(img, job)
    data = Path(rel).read_bytes()
    assert data[:2] == b"\xff\xd8"  # magic bytes JPEG (RGB, bukan RGBA)
    assert Path(rel).exists()


def test_encode_and_save_keeps_alpha_for_webp(monkeypatch, tmp_path):
    """WebP/PNG tetap mempertahankan alpha (ADR-004)."""
    monkeypatch.setattr(settings, "result_dir", str(tmp_path / "results"))
    img = Image.new("RGBA", (16, 16), (10, 20, 30, 128))
    job = Job(
        id="rgba-webp",
        user_id="u",
        status=JobStatus.COMPLETED.value,
        scale=2,
        output_format="webp",
        original_name="x.png",
        original_path="x.png",
    )
    rel = enhance_module._encode_and_save(img, job)
    with Image.open(rel) as saved:
        assert saved.mode == "RGBA"
