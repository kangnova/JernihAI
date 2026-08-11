"""Model ApiKey — API key publik (FR-14 / B2B).

Developer memakai key ini untuk memanggil REST API publik (header
`X-API-Key`). Key asli TIDAK pernah disimpan: hanya hash SHA-256
(`key_hash`) + awalan pendek (`key_prefix`) untuk tampilan di UI.
Tier mengontrol rate limit per menit (lihat settings
`api_rate_limit_free/pro_per_minute`).
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # Pemilik key (User.id) — job B2B tercatat atas nama user ini.
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    # Label bebas dari developer, mis. "Produksi" / "Staging".
    name: Mapped[str] = mapped_column(String(100))
    # SHA-256 hex dari key asli — satu-satunya yang disimpan (unik).
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # Awalan pendek utk ditampilkan, mis. "jn_AbC123xYz" (bukan key asli).
    key_prefix: Mapped[str] = mapped_column(String(20), index=True)
    # Tier: "free" (default) / "pro" — rate limit per menit berbeda.
    tier: Mapped[str] = mapped_column(String(20), default="free", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Terakhir kali dipakai memanggil API (untuk audit).
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Terisi saat key dicabut (revoke) — key nonaktif ditolak 403.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Nota: kolom `usage` jumlah pemakaian di-reserve utk Fase 3 (billing
    # pay-per-call per key); MVP cukup dihitung via job milik user.
