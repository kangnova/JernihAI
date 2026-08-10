"""Kuota gratis harian per user (FR-06).

Reset otomatis berbasis tanggal WIB (Asia/Jakarta) TANPA cron — lazy
reset: saat kuota dicek atau dipakai, bila tanggal tersimpan != tanggal
WIB hari ini, pemakaian di-reset ke 0. Aman karena semua jalur kuota
(cek/konsumsi/refund) wajib lewat helper di sini.

Alur pemakaian: konsumsi 1 kuota saat job diterima (route create_job);
job yang gagal di-refund 1 kuota (lihat app/tasks/enhance.py) supaya
percobaan yang gagal tidak menghabiskan jatah user.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.models.user import User

_WIB = ZoneInfo("Asia/Jakarta")


def wib_today() -> date:
    """Tanggal hari ini di zona waktu Indonesia Barat (WIB)."""
    return datetime.now(_WIB).date()


def quota_limit() -> int:
    return settings.free_daily_quota


def quota_used(user: User) -> int:
    return user.free_daily_quota_used or 0


def _reset_if_needed(user: User) -> None:
    """Reset pemakaian bila tanggal tersimpan != hari ini (WIB)."""
    today = wib_today().isoformat()
    if user.free_quota_date != today:
        user.free_daily_quota_used = 0
        user.free_quota_date = today


def quota_remaining(user: User) -> int:
    _reset_if_needed(user)
    return max(0, quota_limit() - quota_used(user))


def consume_quota(user: User) -> int:
    """Konsumsi 1 kuota (reset lazy dulu). Return sisa kuota.

    Pemanggil bertanggung jawab untuk commit (umumnya digabung dengan
    transaksi pembuatan job di route).
    """
    _reset_if_needed(user)
    user.free_daily_quota_used = quota_used(user) + 1
    return max(0, quota_limit() - user.free_daily_quota_used)


def refund_quota(user: User) -> None:
    """Kembalikan 1 kuota (job gagal); floor di 0."""
    _reset_if_needed(user)
    user.free_daily_quota_used = max(0, quota_used(user) - 1)


# --- Kredit berbayar (FR-11) — dipakai saat kuota gratis habis ---


def credit_balance(user: User) -> int:
    return user.credit_balance or 0


def slots_available(user: User) -> int:
    """Total slot proses tersisa: kuota gratis + saldo kredit."""
    return quota_remaining(user) + credit_balance(user)


def consume_slots(user: User, n: int) -> tuple[int, int]:
    """Konsumsi `n` slot pemrosesan; return (free_used, credit_used).

    Kuota gratis dipakai lebih dulu; sisanya dari kredit (FR-11). Pemanggil
    wajib memastikan `n <= slots_available(user)`; kalau tidak, credit bisa
    minus. Digabung dalam transaksi commit route.
    """
    free_used = min(n, quota_remaining(user))
    credit_used = n - free_used
    for _ in range(free_used):
        consume_quota(user)
    if credit_used:
        user.credit_balance = max(0, credit_balance(user) - credit_used)
    return free_used, credit_used


def refund_credit(user: User, amount: int = 1) -> None:
    """Kembalikan kredit (job berbayar gagal) ke saldo user."""
    user.credit_balance = credit_balance(user) + amount
