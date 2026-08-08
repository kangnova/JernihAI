"""Instance Celery untuk job asinkron (queue GPU).

Catatan: worker memakai pool `solo` (bukan prefork) karena aman untuk
CUDA context — lihat prd.md §9 dan DECISIONS.md ADR-001.
"""

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "jernihai",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks.enhance"],
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
)
