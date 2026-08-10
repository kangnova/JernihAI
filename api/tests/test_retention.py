"""Tests retensi data otomatis (FR-07) — purge original 24 jam & hasil 7 hari.

Menguji fungsi inti `purge_expired` (dipanggil langsung, tanpa Redis):
- Original job selesai/gagal dihapus setelah retention_original_hours.
- Original job queued/processing TIDAK dihapus (masih dibutuhkan proses).
- Hasil job completed dihapus setelah retention_result_days.
- Guard path traversal: file di luar upload_dir/result_dir tidak disentuh.
"""

import pathlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.tasks.retention as retention_module
from app.core.config import settings
from app.models.base import Base
from app.models.job import Job, JobStatus

NOW = datetime.now(UTC)
OLD = NOW - timedelta(days=30)

_counter = 0


def _unique_id() -> str:
    global _counter
    _counter += 1
    return f"j-{_counter}"


@pytest.fixture()
async def db(tmp_path, monkeypatch):
    """SQLite in-memory + storage sementara + factory task = sesi test."""
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "result_dir", str(tmp_path / "results"))
    # Jalur retensi dikontrol langsung di test via args, tapi pastikan
    # pemanggilan memakai factory test (bukan DB produksi).
    monkeypatch.setattr(retention_module, "async_session_factory", factory)

    yield factory
    await engine.dispose()


def _make_file(rel_path: str, content: bytes = b"data") -> str:
    """Tulis file di storage test; return path relatif."""
    path = pathlib.Path(rel_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return rel_path


async def _add_job(db, **kwargs):
    defaults = dict(
        id=_unique_id(),
        user_id="u1",
        status=JobStatus.COMPLETED.value,
        scale=2,
        output_format="webp",
        original_name="foto.png",
        original_path="storage/uploads/ghost.png",
        result_path=None,
        created_at=NOW,
        finished_at=NOW,
    )
    defaults.update(kwargs)
    async with db() as session:
        session.add(Job(**defaults))
        await session.commit()


async def test_purges_old_original_and_marks_deleted(db):
    """Original job selesai yang sudah > 24 jam dihapus + deleted_at terisi."""
    orig = _make_file(f"{settings.upload_dir}/a.png")
    await _add_job(db, id="old-orig", original_path=orig, created_at=OLD)

    stats = await retention_module.purge_expired(now=NOW)

    assert stats == {"original_deleted": 1, "result_deleted": 0}
    assert not pathlib.Path(orig).exists()
    async with db() as session:
        job = await session.get(Job, "old-orig")
        assert job.original_deleted_at is not None
        # Path dipertahankan untuk audit.
        assert job.original_path == orig


async def test_keeps_recent_original(db):
    """Original yang masih muda (baru diunggah) TIDAK dihapus."""
    orig = _make_file(f"{settings.upload_dir}/b.png")
    await _add_job(db, id="new-orig", original_path=orig, created_at=NOW)

    stats = await retention_module.purge_expired(now=NOW)

    assert stats["original_deleted"] == 0
    assert pathlib.Path(orig).exists()


async def test_keeps_original_of_queued_or_processing_job(db):
    """Job queued/processing masih butuh file original — dilindungi."""
    orig = _make_file(f"{settings.upload_dir}/c.png")
    await _add_job(
        db,
        id="busy",
        original_path=orig,
        created_at=OLD,
        status=JobStatus.PROCESSING.value,
    )

    stats = await retention_module.purge_expired(now=NOW)

    assert stats["original_deleted"] == 0
    assert pathlib.Path(orig).exists()


async def test_purges_old_result_after_retention_days(db):
    """Hasil job completed yang sudah > 7 hari dihapus + deleted_at terisi."""
    result = _make_file(f"{settings.result_dir}/x.webp")
    await _add_job(
        db,
        id="old-result",
        result_path=result,
        finished_at=OLD,
    )

    stats = await retention_module.purge_expired(now=NOW)

    assert stats["result_deleted"] == 1
    assert not pathlib.Path(result).exists()
    async with db() as session:
        job = await session.get(Job, "old-result")
        assert job.result_deleted_at is not None
        assert job.result_path == result


async def test_keeps_recent_result(db):
    """Hasil yang masih dalam masa simpan (7 hari) TIDAK dihapus."""
    result = _make_file(f"{settings.result_dir}/y.webp")
    await _add_job(
        db,
        id="new-result",
        result_path=result,
        finished_at=NOW,
    )

    stats = await retention_module.purge_expired(now=NOW)

    assert stats["result_deleted"] == 0
    assert pathlib.Path(result).exists()


async def test_skips_results_without_finished_at(db):
    """Job completed tanpa finished_at tidak dihapus hasilnya (data tak lengkap)."""
    result = _make_file(f"{settings.result_dir}/z.webp")
    await _add_job(
        db,
        id="no-finish",
        result_path=result,
        finished_at=None,
    )

    stats = await retention_module.purge_expired(now=NOW)

    assert stats["result_deleted"] == 0
    assert pathlib.Path(result).exists()


async def test_does_not_touch_files_outside_storage(db, tmp_path):
    """Guard traversal: file di luar upload_dir/result_dir tidak dihapus."""
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"data")

    await _add_job(db, id="evil", original_path=str(outside), created_at=OLD)
    stats = await retention_module.purge_expired(now=NOW)

    assert stats["original_deleted"] == 0
    assert outside.exists()


async def test_idempotent_second_run(db):
    """Sweep dua kali: file yang sudah ditandai tidak dihapus ulang."""
    orig = _make_file(f"{settings.upload_dir}/d.png")
    await _add_job(db, id="twice", original_path=orig, created_at=OLD)

    first = await retention_module.purge_expired(now=NOW)
    second = await retention_module.purge_expired(now=NOW)

    assert first["original_deleted"] == 1
    assert second["original_deleted"] == 0


async def test_already_deleted_marker_is_respected(db):
    """Job yang sudah ditandai original_deleted_at tidak di-scan ulang."""
    orig = _make_file(f"{settings.upload_dir}/e.png")
    await _add_job(
        db,
        id="marked",
        original_path=orig,
        created_at=OLD,
        original_deleted_at=NOW,  # sudah dihapus di sweep sebelumnya
    )

    stats = await retention_module.purge_expired(now=NOW)

    assert stats["original_deleted"] == 0
