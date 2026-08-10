"""Pydantic schemas untuk endpoint auth (register/login/me)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=255)
    # FR-07 (UU PDP): persetujuan kebijakan privasi — WAJIB True (default
    # False supaya pesan penolakan eksplisit dari route, bukan 422 validasi
    # generik Pydantic saat field tidak dikirim).
    privacy_consent: bool = Field(
        default=False, description="Persetujuan kebijakan privasi (FR-07)"
    )


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    name: str | None
    provider: str
    # Kapan user menyetujui kebijakan privasi (None = belum, FR-07).
    privacy_consent_at: datetime | None = None
    # FR-13: apakah email user terdaftar di ADMIN_EMAILS (property model).
    is_admin: bool = False
    created_at: datetime
