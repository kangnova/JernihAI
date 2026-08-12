"""Instance Celery untuk job asinkron (queue GPU) + jadwal beat retensi.

Catatan: worker memakai pool `solo` (bukan prefork) karena aman untuk
CUDA context — lihat prd.md §9 dan DECISIONS.md ADR-001.

Jadwal Beat (FR-07): `retention.purge_expired` dijalankan berkala oleh
proses beat terpisah (service `beat` di docker-compose) untuk auto-delete
file kedaluwarsa — original 24 jam & hasil 7 hari.
"""

from celery import Celery
from celery.schedules import schedule

from app.core.config import settings

celery_app = Celery(
    "jernihai",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks.enhance", "app.tasks.retention", "app.tasks.stale"],
)

celery_app.conf.update(
    timezone="Asia/Jakarta",
    enable_utc=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Job GPU panjang: ack setelah selesai + prefetch 1 agar job tidak
    # dobel-diproses saat worker restart.
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # NFR-03: timeout per job (prd.md §8) — soft 120 dtk (interupsi pipeline
    # yang hang -> job failed -> retry otomatis), hard 180 dtk (bunuh paksa
    # task yang mengabaikan soft limit). Worker yang crash tetap ditangkap
    # stale-check (app/tasks/stale.py).
    task_soft_time_limit=settings.job_soft_time_limit_seconds,
    task_time_limit=settings.job_hard_time_limit_seconds,
    # NFR-03: bila worker hilang (crash/reboot) di tengah task yang sudah
    # di-ack, task dikirim ulang ke worker lain (bukan hilang selamanya).
    task_reject_on_worker_lost=True,
    task_acks_on_failure_or_timeout=False,
    # Fase 3 (multi-instance): worker TIDAK crash-loop saat Redis sempat
    # restart bersamaan (scale up / rolling deploy) — tetap mencoba
    # menyambung broker sampai berhasil.
    broker_connection_retry_on_startup=True,
)

# Jadwal berkala (proses Celery Beat terpisah, lihat docker-compose
# service `beat`):
# - FR-07: sweep retensi file kedaluwarsa (interval RETENTION_PURGE_INTERVAL_MINUTES).
# - NFR-03: stale-check job yang stuck di status processing (interval
#   STALE_CHECK_INTERVAL_MINUTES) — ini juga membuka jalan retensi untuk
#   menghapus original job stuck (bocor disk).
celery_app.conf.beat_schedule = {
    "purge-expired-files": {
        "task": "retention.purge_expired",
        "schedule": schedule(
            run_every=settings.retention_purge_interval_minutes * 60
        ),
    },
    "recover-stale-jobs": {
        "task": "jobs.recover_stale",
        "schedule": schedule(
            run_every=settings.stale_check_interval_minutes * 60
        ),
    },
}
