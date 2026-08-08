"""Task enhancement — stub Fase 0.

Implementasi nyata (Real-ESRGAN / CodeFormer) menunggu keputusan
arsitektur GPU — lihat DECISIONS.md ADR-001 & ADR-002.
"""

from app.tasks.worker import celery_app


@celery_app.task(name="enhance.process", bind=True, max_retries=2)
def process_enhancement(self, job_id: str) -> dict[str, str]:
    """Stub: Fase 2 akan memanggil pipeline GPU (tiling + FP16) di sini."""
    return {"job_id": job_id, "status": "stub"}
