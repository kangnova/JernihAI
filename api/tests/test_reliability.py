"""Tests reliabilitas job (NFR-03) — retry otomatis max 2x + stale-check.

Cakupan:
- `process_job`: force_retry (proses ulang job failed), refund_on_fail
  (refund hanya di percobaan terakhir).
- `process_enhancement`: kebijakan retry Celery (force_retry/refund per
  percobaan, berhenti saat max_retries tercapai).
- `recover_stale_jobs`: job processing yang hang -> failed + refund kuota;
  idempoten; tidak menyentuh job sehat.
- Integrasi: stale-check membuka jalan retensi (original job stuck akhirnya
  bisa dihapus FR-07) — menutup kebocoran disk.
"""

import io
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.tasks.enhance as enhance_module
import app.tasks.stale as stale_module
from app.core.config import settings
from app.core.quota import refund_quota
from app.db.session import get_db
from app.main import app
from app.models.base import Base
from app.models.job import Job, JobStatus
from app.models.user import User

NOW = datetime.now(UTC)
OLD = NOW - timedelta(hours=1)  # > job_stale_minutes (30)
ANCIENT = NOW - timedelta(days=2)  # > retensi original (24 jam)

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

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(settings, "celery_task_always_eager", True)
    monkeypatch.setattr(settings, "enhance_backend", "mock")
    monkeypatch.setattr(settings, "rate_limit_enabled", False)  # NFR-04
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "result_dir", str(tmp_path / "results"))
    monkeypatch.setattr(enhance_module, "async_session_factory", factory)
    monkeypatch.setattr(stale_module, "async_session_factory", factory)

    yield factory

    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.fixture()
async def client(db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _image_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 100, 50)).save(buf, format="PNG")
    return buf.getvalue()


async def _register(client, email: str = "u@example.com") -> str:
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


def _write_upload(tmp_path, name: str = "foto.png") -> str:
    path = Path(settings.upload_dir) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_image_bytes())
    return str(path)


async def _add_job(db, **kwargs):
    """Insert job langsung (status bebas); return id."""
    defaults = dict(
        id=_unique_id(),
        user_id="u1",
        status=JobStatus.QUEUED.value,
        scale=2,
        output_format="webp",
        original_name="foto.png",
        original_path="storage/uploads/ghost.png",
        result_path=None,
        created_at=NOW,
        updated_at=NOW,
        finished_at=None,
    )
    defaults.update(kwargs)
    async with db() as session:
        session.add(Job(**defaults))
        await session.commit()
    return defaults["id"]


async def _user_with_used_quota(db, used: int = 1) -> str:
    async with db() as session:
        user = User(
            id="u-quota",
            email="quota@example.com",
            password_hash="x",
            free_daily_quota_used=used,
            free_quota_date="1970-01-01",
            privacy_consent_at=NOW,
        )
        session.add(user)
        await session.commit()
    return "u-quota"


async def _quota_used(db, user_id: str) -> int:
    async with db() as session:
        user = await session.get(User, user_id)
        return user.free_daily_quota_used


# --- process_job: force_retry & refund_on_fail ---


async def test_process_job_rejects_failed_without_force_retry(db):
    """Job failed TIDAK diproses ulang tanpa force_retry (mencegah duplikat)."""
    job_id = await _add_job(db, status=JobStatus.FAILED.value)

    result = await enhance_module.process_job(job_id)

    assert result is None
    async with db() as session:
        job = await session.get(Job, job_id)
        assert job.status == JobStatus.FAILED.value


async def test_process_job_force_retry_reprocesses_failed(db, tmp_path):
    """force_retry=True memproses ulang job failed sampai sukses."""
    orig = _write_upload(tmp_path, "retry.png")
    job_id = await _add_job(
        db, status=JobStatus.FAILED.value, original_path=orig
    )

    result = await enhance_module.process_job(job_id, force_retry=True)

    assert result == JobStatus.COMPLETED.value
    async with db() as session:
        job = await session.get(Job, job_id)
        assert job.status == JobStatus.COMPLETED.value
        assert job.error is None  # error lama dibersihkan saat retry


async def test_process_job_refund_only_when_refund_on_fail(db, tmp_path):
    """refund_on_fail=False (percobaan retry) TIDAK refund kuota."""
    user_id = await _user_with_used_quota(db)
    orig = _write_upload(tmp_path, "fail1.png")
    job_id = await _add_job(
        db,
        user_id=user_id,
        status=JobStatus.QUEUED.value,
        original_path=orig,
    )
    # Simulasikan kegagalan pipeline: original dihapus.
    Path(orig).unlink()

    await enhance_module.process_job(job_id, refund_on_fail=False)

    assert await _quota_used(db, user_id) == 1  # tetap 1, tidak refund
    async with db() as session:
        job = await session.get(Job, job_id)
        assert job.status == JobStatus.FAILED.value


async def test_process_job_refund_on_final_attempt(db, tmp_path):
    """refund_on_fail=True (percobaan terakhir) mengembalikan kuota."""
    user_id = await _user_with_used_quota(db)
    orig = _write_upload(tmp_path, "final.png")
    job_id = await _add_job(db, user_id=user_id, original_path=orig)
    Path(orig).unlink()  # force failure

    await enhance_module.process_job(job_id, refund_on_fail=True)

    assert await _quota_used(db, user_id) == 0  # di-refund


# --- Fase 3 (multi-instance): klaim atomik & guard race stale-check ---


async def test_redelivery_skips_job_claimed_by_other_worker(db):
    """Redelivery: job sudah `processing` (dipegang worker lain) -> skip.

    Worker mati di tengah -> broker redeliver (acks_late +
    reject_on_worker_lost); worker kedua TIDAK boleh memproses ulang job
    yang sama (double-process / double-refund).
    """
    job_id = await _add_job(db, status=JobStatus.PROCESSING.value)

    result = await enhance_module.process_job(job_id)

    assert result is None  # "skipped" — bukan diproses ulang
    async with db() as session:
        job = await session.get(Job, job_id)
        assert job.status == JobStatus.PROCESSING.value  # tak berubah


async def test_claim_atomic_only_one_worker_wins(db):
    """Klaim atomik: transisi status via SQL guard — yang kalah tak dapat job."""
    job_id = await _add_job(db, status=JobStatus.PROCESSING.value)
    async with db() as session:
        job = await enhance_module._claim_job(session, job_id, force_retry=False)
        assert job is None  # status bukan queued -> klaim ditolak


async def test_redelivery_after_failure_no_double_refund(db, tmp_path):
    """Redelivery setelah gagal: refund TIDAK dobel (guard `_fail_job`)."""
    async with db() as session:
        session.add(
            User(
                id="u-dbl",
                email="dbl@example.com",
                password_hash="x",
                credit_balance=0,  # 1 kredit sudah dipakai job ini
                free_daily_quota_used=0,
                free_quota_date="1970-01-01",
                privacy_consent_at=NOW,
            )
        )
        await session.commit()
    orig = _write_upload(tmp_path, "dbl.png")
    job_id = await _add_job(
        db, user_id="u-dbl", uses_credit=True, original_path=orig,
    )
    Path(orig).unlink()  # force failure

    first = await enhance_module.process_job(job_id, refund_on_fail=True)
    assert first == JobStatus.FAILED.value

    # Broker redeliver pesan yang sama -> worker kedua memanggil process_job
    # TANPA force_retry: klaim ditolak, refund tidak dijalankan lagi.
    again = await enhance_module.process_job(job_id, refund_on_fail=True)
    assert again is None

    async with db() as session:
        user = await session.get(User, "u-dbl")
        assert user.credit_balance == 1  # refund TEPAT SEKALI, bukan 2


async def test_stale_race_completion_does_not_overwrite(db, tmp_path):
    """Race stale-check vs worker: guard selesai mencegah double-benefit.

    Stale-check menandai job `failed` + refund saat worker masih memproses;
    worker yang selesai TIDAK boleh menimpa status (user tidak boleh dapat
    hasil + refund sekaligus).
    """
    user_id = await _user_with_used_quota(db)
    orig = _write_upload(tmp_path, "race.png")
    job_id = await _add_job(db, user_id=user_id, original_path=orig)

    # Simulasi stale-check yang menang duluan: failed + refund.
    async with db() as session:
        job = await session.get(Job, job_id)
        job.status = JobStatus.FAILED.value
        job.error = "Waktu pemrosesan habis (NFR-03)"
        user = await session.get(User, user_id)
        refund_quota(user)
        await session.commit()

    # Worker 'selesai' -> completion guard: job bukan processing lagi -> tolak.
    async with db() as session:
        ok = await enhance_module._complete_job(
            session, job_id, f"{settings.result_dir}/race.webp"
        )
        await session.commit()

    assert ok is False
    async with db() as session:
        job = await session.get(Job, job_id)
        assert job.status == JobStatus.FAILED.value  # tidak ditimpa
        assert "NFR-03" in job.error  # pesan stale-check dipertahankan
        assert job.result_path is None  # hasil tidak direferensikan
    assert await _quota_used(db, user_id) == 0  # refund tetap 1x


# --- process_enhancement: kebijakan retry Celery ---


class _RetryRaised(Exception):
    """Sentinel: self.retry() dipanggil."""


class _FakeTask:
    """Tiruan task Celery untuk menguji logika retry tanpa broker."""

    class _Request:
        retries = 0

    def __init__(self, retries: int, max_retries: int = 2):
        self.request = self._Request()
        self.request.retries = retries
        self.max_retries = max_retries

    def retry(self, *args, **kwargs):
        raise _RetryRaised()


def _call_task(task_fake, job_id: str) -> dict:
    """Panggil body task Celery dengan self tiruan di THREAD terpisah.

    Body task memakai `asyncio.run` (pola worker Celery) yang tidak bisa
    dipanggil dari dalam running event loop test async — karena itu dijalankan
    di thread baru, sama seperti eksekusi worker asli.

    `task.run` pada bind=True sudah ter-bound ke instance task, jadi dipakai
    `run.__func__` (fungsi dasar) lalu self tiruan dikirim eksplisit.
    """
    out: list[dict] = []
    raised: list[BaseException] = []

    def _run():
        try:
            out.append(
                enhance_module.process_enhancement.run.__func__(task_fake, job_id)
            )
        except BaseException as exc:  # noqa: BLE001 — teruskan ke thread utama
            raised.append(exc)

    thread = threading.Thread(target=_run)
    thread.start()
    thread.join()
    if raised:
        raise raised[0]
    return out[0]


async def test_task_decorator_has_nfr03_time_limits():
    """NFR-03: task enhance memakai timeout per job (soft 120s, hard 180s)."""
    task = enhance_module.process_enhancement
    assert task.soft_time_limit == settings.job_soft_time_limit_seconds
    assert task.time_limit == settings.job_hard_time_limit_seconds
    # Default Celery global di worker.py konsisten dengan settings.
    conf = enhance_module.celery_app.conf
    assert conf.task_soft_time_limit == settings.job_soft_time_limit_seconds
    assert conf.task_time_limit == settings.job_hard_time_limit_seconds
    assert conf.task_reject_on_worker_lost is True


async def test_retry_policy_flags_per_attempt(db, tmp_path, monkeypatch):
    """force_retry & refund_on_fail dihitung benar per percobaan."""
    captured: list[dict] = []

    async def fake_process_job(job_id, *, force_retry, refund_on_fail):
        captured.append(
            {"force_retry": force_retry, "refund_on_fail": refund_on_fail}
        )
        return JobStatus.FAILED.value

    monkeypatch.setattr(enhance_module, "process_job", fake_process_job)

    # Percobaan 1 (retries=0): bukan retry -> tanpa force, tanpa refund.
    await _add_job(db)
    with pytest.raises(_RetryRaised):
        _call_task(_FakeTask(retries=0), "j-x")
    # Percobaan 2 (retries=1): retry -> force, masih tanpa refund.
    with pytest.raises(_RetryRaised):
        _call_task(_FakeTask(retries=1), "j-x")
    # Percobaan terakhir (retries=2 = max): force + refund; tidak retry lagi.
    result = _call_task(_FakeTask(retries=2), "j-x")

    assert captured == [
        {"force_retry": False, "refund_on_fail": False},
        {"force_retry": True, "refund_on_fail": False},
        {"force_retry": True, "refund_on_fail": True},
    ]
    assert result == {"job_id": "j-x", "status": "failed"}


async def test_retry_raises_when_failed_and_retries_left(db, tmp_path, monkeypatch):
    async def fake_process_job(job_id, *, force_retry, refund_on_fail):
        return JobStatus.FAILED.value

    monkeypatch.setattr(enhance_module, "process_job", fake_process_job)
    await _add_job(db)

    with pytest.raises(_RetryRaised):
        _call_task(_FakeTask(retries=0), "j-x")


async def test_no_retry_on_success(db, tmp_path, monkeypatch):
    async def fake_process_job(job_id, *, force_retry, refund_on_fail):
        return JobStatus.COMPLETED.value

    monkeypatch.setattr(enhance_module, "process_job", fake_process_job)
    await _add_job(db)

    result = _call_task(_FakeTask(retries=0), "j-x")
    assert result == {"job_id": "j-x", "status": "completed"}


# --- recover_stale_jobs ---


async def test_stale_processing_job_recovered_and_refunded(db):
    """Job processing yang hang (> stale_minutes) -> failed + refund kuota."""
    user_id = await _user_with_used_quota(db)
    job_id = await _add_job(
        db,
        user_id=user_id,
        status=JobStatus.PROCESSING.value,
        updated_at=OLD,
    )

    stats = await stale_module.recover_stale_jobs(now=NOW)

    assert stats == {"recovered": 1}
    async with db() as session:
        job = await session.get(Job, job_id)
        assert job.status == JobStatus.FAILED.value
        assert "NFR-03" in (job.error or "")
    assert await _quota_used(db, user_id) == 0


async def test_fresh_processing_job_not_touched(db):
    """Job processing yang masih muda tidak disentuh."""
    job_id = await _add_job(
        db, status=JobStatus.PROCESSING.value, updated_at=NOW
    )

    stats = await stale_module.recover_stale_jobs(now=NOW)

    assert stats == {"recovered": 0}
    async with db() as session:
        job = await session.get(Job, job_id)
        assert job.status == JobStatus.PROCESSING.value


async def test_stale_check_idempotent(db):
    """Job yang sudah dipulihkan tidak disentuh ulang."""
    user_id = await _user_with_used_quota(db)
    await _add_job(
        db,
        user_id=user_id,
        status=JobStatus.PROCESSING.value,
        updated_at=OLD,
    )

    first = await stale_module.recover_stale_jobs(now=NOW)
    second = await stale_module.recover_stale_jobs(now=NOW)

    assert first == {"recovered": 1}
    assert second == {"recovered": 0}


async def test_stale_check_does_not_touch_other_statuses(db):
    """Job completed/queued/failed tidak dipulihkan oleh stale-check."""
    for status in (
        JobStatus.COMPLETED.value,
        JobStatus.QUEUED.value,
        JobStatus.FAILED.value,
    ):
        await _add_job(db, status=status, updated_at=OLD)

    stats = await stale_module.recover_stale_jobs(now=NOW)

    assert stats == {"recovered": 0}


async def test_stale_paid_job_refunds_credit(db):
    """FR-11: job BERBAYAR yang hang -> stale-check refund KREDIT, bukan kuota.

    Job memakai kredit (uses_credit=True, free quota 0) yang stuck di
    `processing` harus mengembalikan 1 kredit ke saldo user — kuota gratis
    tidak boleh disentuh (refund sesuai sumber pembayaran).
    """
    async with db() as session:
        user = User(
            id="u-paid",
            email="paid@example.com",
            password_hash="x",
            credit_balance=0,  # 1 kredit sudah dikonsumsi job ini
            free_daily_quota_used=0,
            free_quota_date="1970-01-01",
            privacy_consent_at=NOW,
        )
        session.add(user)
        await session.commit()
    job_id = await _add_job(
        db,
        user_id="u-paid",
        status=JobStatus.PROCESSING.value,
        updated_at=OLD,
        uses_credit=True,
    )

    stats = await stale_module.recover_stale_jobs(now=NOW)

    assert stats == {"recovered": 1}
    async with db() as session:
        user = await session.get(User, "u-paid")
        assert user.credit_balance == 1  # kredit dikembalikan ke saldo
        assert user.free_daily_quota_used == 0  # kuota gratis tidak disentuh
        job = await session.get(Job, job_id)
        assert job.status == JobStatus.FAILED.value


# --- Integrasi: stale-check membuka jalan retensi (anti bocor disk) ---


async def test_stale_then_retention_purges_original(db, tmp_path, monkeypatch):
    """Alur lengkap: job hang -> stale-check failed -> retensi hapus original.

    Menutup kebocoran disk: tanpa stale-check, original job stuck di
    `processing` tidak pernah dihapus retensi (FR-07 hanya menyentuh
    completed/failed).
    """
    import app.tasks.retention as retention_module

    monkeypatch.setattr(
        retention_module, "async_session_factory", stale_module.async_session_factory
    )

    orig = _write_upload(tmp_path, "stuck.png")
    job_id = await _add_job(
        db,
        status=JobStatus.PROCESSING.value,
        original_path=orig,
        created_at=ANCIENT,  # original sudah > 24 jam (syarat retensi)
        updated_at=OLD,      # hang > 30 menit (syarat stale-check)
    )

    # 1) Stale-check: pulihkan job hang menjadi failed.
    stats = await stale_module.recover_stale_jobs(now=NOW)
    assert stats == {"recovered": 1}

    # 2) Retensi: original job failed yang kedaluwarsa dihapus.
    purge = await retention_module.purge_expired(now=NOW)
    assert purge["original_deleted"] == 1
    assert not Path(orig).exists()

    async with db() as session:
        job = await session.get(Job, job_id)
        assert job.original_deleted_at is not None
