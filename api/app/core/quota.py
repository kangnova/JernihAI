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
