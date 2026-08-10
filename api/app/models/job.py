"""Model Job — job pemrosesan gambar (prd.md: FR-02/FR-03, state machine)."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class JobStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(
        String(20), default=JobStatus.QUEUED.value, index=True
    )
    scale: Mapped[int] = mapped_column(Integer, default=2)
    output_format: Mapped[str] = mapped_column(String(10), default="webp")
    # FR-08: restorasi wajah GFPGAN (toggle opsional per job).
    face_enhance: Mapped[bool] = mapped_column(default=False)
    # FR-09: pra-pemrosesan opsional per job — denoise (realesr-general-x4v3
    # + wdn, DNI) dan color enhance (autocontrast + saturasi).
    denoise: Mapped[bool] = mapped_column(default=False)
    color_enhance: Mapped[bool] = mapped_column(default=False)
    # FR-11: job ini membayar memakai 1 KREDIT berbayar (bukan kuota gratis).
    # Dipakai refund saat job gagal: kredit dikembalikan ke saldo user.
    uses_credit: Mapped[bool] = mapped_column(default=False)
    original_name: Mapped[str] = mapped_column(String(255))
    # Path relatif terhadap folder storage (lihat app/core/storage.py).
    # NOTE (FR-07): setelah purge retensi, file dihapus dari disk dan
    # timestamp deleted_at terisi; path dipertahankan untuk audit.
    original_path: Mapped[str] = mapped_column(String(500))
    result_path: Mapped[str | None] = mapped_column(String(500))
    # FR-07: kapan file original / hasil dihapus oleh retensi (None = masih ada).
    original_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(500))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
