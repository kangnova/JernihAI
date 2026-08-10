"""Pydantic schemas untuk endpoint jobs (upload/status/download)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    scale: int
    output_format: str
    face_enhance: bool = False
    denoise: bool = False
    color_enhance: bool = False
    original_name: str
    error: str | None
    created_at: datetime
    finished_at: datetime | None
    # FR-07/FR-10: kapan hasil dihapus retensi (None = masih tersedia).
    # Dipakai UI riwayat untuk menonaktifkan tombol unduh ulang.
    result_deleted_at: datetime | None = None


class JobListOut(BaseModel):
    """Halaman riwayat job user (FR-10)."""

    items: list[JobOut]
    total: int
