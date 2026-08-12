from datetime import datetime

from pydantic import BaseModel


class CheckResult(BaseModel):
    """Hasil cek satu dependensi (readiness, NFR-08)."""

    name: str  # mis. "postgres" / "redis"
    status: str  # "ok" | "fail" | "skipped"
    detail: str | None = None


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    environment: str
    # Detail per dependensi — kosong untuk liveness (tanpa cek).
    checks: list[CheckResult] = []


# --- Metrik operasional (NFR-08) ---


class QueueMetric(BaseModel):
    """Panjang antrean Celery dari Redis (LLEN) — sinyal autoscale.

    `status`: "ok" (length terisi) | "skipped" (mode eager tanpa broker) |
    "error" (Redis tidak terjangkau — endpoint tetap 200, pemantau yang
    menilai).
    """

    status: str
    length: int | None = None
    detail: str | None = None


class JobCounts(BaseModel):
    """Snapshot jumlah job per status saat ini."""

    queued: int
    processing: int
    completed: int
    failed: int


class ThroughputMetric(BaseModel):
    """Laju job pada jendela waktu (anchor `updated_at` = transisi terakhir).

    `failure_rate_24h` = failed / (failed + completed) pada 24 jam terakhir;
    None bila tidak ada job selesai dalam jendela (data tidak cukup).
    """

    completed_1h: int
    completed_24h: int
    failed_24h: int
    failure_rate_24h: float | None = None


class LatencyMetric(BaseModel):
    """Durasi proses rata-rata dari job `completed` 24 jam terakhir."""

    avg_processing_seconds_24h: float | None = None
    samples: int


class ConfigInfo(BaseModel):
    """Info konfigurasi penting untuk konteks metrik (ops)."""

    environment: str
    storage_backend: str
    rate_limit_backend: str
    enhance_backend: str


class MetricsResponse(BaseModel):
    service: str
    version: str
    generated_at: datetime
    queue: QueueMetric
    jobs: JobCounts
    throughput: ThroughputMetric
    latency: LatencyMetric
    config: ConfigInfo
