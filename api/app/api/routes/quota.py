"""Endpoint kuota gratis (FR-06) — sisa kuota harian user yang login.

Dipakai dashboard web untuk menampilkan jatah gratis dan menonaktifkan
upload saat kuota habis.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.core.quota import credit_balance, quota_limit, quota_remaining, wib_today
from app.models.user import User

router = APIRouter(prefix="/quota", tags=["quota"])


class QuotaOut(BaseModel):
    limit: int
    used: int
    remaining: int
    reset_date: str
    # FR-11: saldo kredit berbayar + total slot (gratis + kredit) — dipakai
    # UI untuk menampilkan opsi "Beli kredit" dan menghitung slot batch.
    credit_balance: int = 0
    total_slots: int = 0


@router.get("", response_model=QuotaOut, summary="Sisa kuota gratis hari ini (FR-06)")
async def get_quota(current_user: User = Depends(get_current_user)) -> QuotaOut:
    # `quota_remaining` melakukan lazy reset bila sudah ganti hari; `used`
    # dihitung dari `remaining` supaya selalu konsisten (tidak bergantung
    # urutan pemanggilan).
    remaining = quota_remaining(current_user)
    return QuotaOut(
        limit=quota_limit(),
        used=quota_limit() - remaining,
        remaining=remaining,
        reset_date=wib_today().isoformat(),
        credit_balance=credit_balance(current_user),
        total_slots=remaining + credit_balance(current_user),
    )
