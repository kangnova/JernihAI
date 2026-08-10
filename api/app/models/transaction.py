"""Model Transaction — transaksi pembelian kredit (FR-11).

Satu baris = satu order pembayaran yang dibuat user; status mengikuti
notifikasi webhook gateway. `order_id` (unik per user) dipakai sebagai
kunci idempotensi: webhook duplikat tidak menambah saldo dua kali.
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TransactionStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    EXPIRED = "expired"


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    # order_id dikirim ke gateway (format `<user_short>-<uuid>`); unik
    # per user → idempotensi webhook.
    order_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(20), default="midtrans")
    # ID transaksi dari gateway (terisi saat notifikasi sukses tiba).
    provider_txn_id: Mapped[str | None] = mapped_column(String(64), index=True)
    package_slug: Mapped[str] = mapped_column(String(50))
    amount_idr: Mapped[int] = mapped_column(Integer)
    credits: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(20), default=TransactionStatus.PENDING.value, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
