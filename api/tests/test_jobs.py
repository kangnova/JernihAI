"""Tests jobs — upload, validasi konten (magic bytes), status, download (Fase 1).

Pipeline dijalankan inline (mode eager) sehingga end-to-end
(register → upload → job selesai → download) diuji tanpa Redis/worker.
"""

import io
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
        json={"email": email, "password": "password123", "name": "Tono"},
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
    monkeypatch.setattr(enhance_module, "_get_upsampler", lambda: None)
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
    monkeypatch.setattr(enhance_module, "_get_upsampler", lambda: None)

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
