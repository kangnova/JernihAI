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
