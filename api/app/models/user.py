"""Model User — akun pengguna (prd.md FR-01).

Mendukung dua jalur auth: email+password lokal dan Google OAuth.
Untuk OAuth, `password_hash` NULL dan `provider`/`provider_sub` terisi.
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.models.base import Base


class AuthProvider(StrEnum):
    LOCAL = "local"
    GOOGLE = "google"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    provider: Mapped[str] = mapped_column(
        String(20), default=AuthProvider.LOCAL.value, index=True
    )
    provider_sub: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)
    # Kuota gratis harian (FR-06) — reset lazy berbasis tanggal WIB,
    # lihat app/core/quota.py. `free_quota_date` = tanggal WIB terakhir
    # reset, format "YYYY-MM-DD" (sentinel 1970-01-01 = belum pernah).
    free_daily_quota_used: Mapped[int] = mapped_column(Integer, default=0)
    free_quota_date: Mapped[str] = mapped_column(String(10), default="1970-01-01")
    # FR-11: saldo kredit berbayar (1 kredit = 1 gambar). Dipakai saat
    # kuota gratis habis; diisi oleh webhook pembayaran sukses.
    credit_balance: Mapped[int] = mapped_column(Integer, default=0)
    # FR-07 (UU PDP): timestamp persetujuan kebijakan privasi (kapan user
    # menyetujui; None = belum menyetujui). Consent wajib saat register
    # lokal; user Google OAuth diminta lewat banner di dashboard.
    privacy_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def is_admin(self) -> bool:
        """FR-13: admin ditentukan dari daftar email env (ADMIN_EMAILS).

        Property (bukan kolom) agar tidak perlu migrasi; terbaca oleh
        `UserOut` via from_attributes.
        """
        return (self.email or "").lower() in {e.lower() for e in settings.admin_emails}
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
