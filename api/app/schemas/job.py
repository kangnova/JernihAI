"""Pydantic schemas untuk endpoint jobs (upload/status/download)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    scale: int
    output_format: str
    original_name: str
    error: str | None
    created_at: datetime
    finished_at: datetime | None
