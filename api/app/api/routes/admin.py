"""Admin dashboard API (FR-13) — monitoring user, job, revenue.

Hanya untuk email yang terdaftar di `ADMIN_EMAILS` (lihat
`app.api.deps.require_admin`). Revenue diisi FR-11 (pembayaran); saat ini
mengembalikan nol.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.config import settings
from app.core.quota import quota_limit, quota_remaining, wib_today
from app.core.storage import delete_if_inside
from app.db.session import get_db
from app.models.job import Job, JobStatus
from app.models.transaction import Transaction
from app.models.user import User

router = APIRouter(prefix="/admin", tags=["admin"])


class AdminStats(BaseModel):
    total_users: int
    users_today: int
    total_jobs: int
    jobs_by_status: dict[str, int]
    jobs_today: int
    free_quota_limit: int
    revenue_idr: int = 0  # FR-11: diisi setelah pembayaran aktif


class AdminJobOut(BaseModel):
    id: str
    user_email: str | None
    status: str
    scale: int
    output_format: str
    original_name: str
    created_at: str | None
    finished_at: str | None
    error: str | None


class AdminJobList(BaseModel):
    items: list[AdminJobOut]
    total: int


class AdminUserOut(BaseModel):
    """Profil user + pemakaian kuota/kredit + jumlah riwayat (FR-13)."""
    id: str
    email: str
    name: str | None
    provider: str
    is_active: bool
    created_at: str | None
    privacy_consent_at: str | None
    quota_used: int
    quota_limit: int
    quota_remaining: int
    credit_balance: int
    job_count: int


class AdminUserList(BaseModel):
    items: list[AdminUserOut]
    total: int


class AdminTransactionOut(BaseModel):
    """Transaksi kredit (FR-11) milik satu user — untuk detail user admin."""
    id: str
    order_id: str
    provider: str
    package_slug: str
    amount_idr: int
    credits: int
    status: str
    created_at: str | None
    paid_at: str | None


class AdminTransactionList(BaseModel):
    items: list[AdminTransactionOut]
    total: int


class QuotaResetRequest(BaseModel):
    """Target reset kuota: `email` satu user ATAU `all=true` (semua)."""
    email: str | None = None
    all: bool = False


class QuotaResetOut(BaseModel):
    reset: int
    email: str | None


def _admin_user_out(user: User, job_count: int) -> AdminUserOut:
    """Bangun AdminUserOut — dipakai daftar user & detail satu user."""
    return AdminUserOut(
        id=user.id,
        email=user.email,
        name=user.name,
        provider=user.provider,
        is_active=user.is_active,
        created_at=user.created_at.isoformat() if user.created_at else None,
        privacy_consent_at=(
            user.privacy_consent_at.isoformat()
            if user.privacy_consent_at
            else None
        ),
        quota_used=user.free_daily_quota_used or 0,
        quota_limit=quota_limit(),
        quota_remaining=quota_remaining(user),  # lazy reset WIB (FR-06)
        credit_balance=user.credit_balance or 0,
        job_count=job_count,
    )


@router.get(
    "/stats",
    response_model=AdminStats,
    summary="Statistik platform (FR-13)",
)
async def admin_stats(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> AdminStats:
    today_start = datetime.now(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    total_users = await db.scalar(select(func.count()).select_from(User)) or 0
    users_today = (
        await db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.created_at >= today_start)
        )
        or 0
    )
    total_jobs = await db.scalar(select(func.count()).select_from(Job)) or 0
    jobs_today = (
        await db.scalar(
            select(func.count()).select_from(Job).where(Job.created_at >= today_start)
        )
        or 0
    )

    status_rows = await db.execute(
        select(Job.status, func.count()).group_by(Job.status)
    )
    jobs_by_status = {status: count for status, count in status_rows.all()}
    for s in JobStatus:
        jobs_by_status.setdefault(s.value, 0)

    return AdminStats(
        total_users=total_users,
        users_today=users_today,
        total_jobs=total_jobs,
        jobs_by_status=jobs_by_status,
        jobs_today=jobs_today,
        free_quota_limit=quota_limit(),
    )


@router.get(
    "/jobs",
    response_model=AdminJobList,
    summary="Semua job lintas user (FR-13)",
)
async def admin_jobs(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    email: str | None = Query(None, max_length=255),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> AdminJobList:
    """Daftar job (terbaru dulu) — semua user atau riwayat SATU user (`email`).

    `email` = pencocokan persis (email unik) — dipakai admin melihat
    riwayat lengkap milik user tertentu.
    """
    total = await db.scalar(select(func.count()).select_from(Job)) or 0
    rows_query = (
        select(Job, User.email)
        .join(User, Job.user_id == User.id, isouter=True)
        .order_by(Job.created_at.desc(), Job.id.desc())
    )
    if email:
        total = (
            await db.scalar(
                select(func.count())
                .select_from(Job)
                .join(User, Job.user_id == User.id, isouter=True)
                .where(User.email == email)
            )
            or 0
        )
        rows_query = rows_query.where(User.email == email)
    rows = await db.execute(rows_query.offset(offset).limit(limit))
    items = [
        AdminJobOut(
            id=job.id,
            user_email=email,
            status=job.status,
            scale=job.scale,
            output_format=job.output_format,
            original_name=job.original_name,
            created_at=job.created_at.isoformat() if job.created_at else None,
            finished_at=job.finished_at.isoformat() if job.finished_at else None,
            error=job.error,
        )
        for job, email in rows.all()
    ]
    return AdminJobList(items=items, total=total)


@router.get(
    "/users",
    response_model=AdminUserList,
    summary="Daftar user + kuota & kredit (FR-13)",
)
async def admin_users(
    email: str | None = Query(None, max_length=255),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> AdminUserList:
    """Direktori user: email, nama, provider, kuota gratis (FR-06), saldo
    kredit (FR-11), consent privasi (FR-07), dan jumlah riwayat job.

    `email` = pencarian parsial (ilike); urut terbaru dulu.
    """
    filters = [User.email.ilike(f"%{email}%")] if email else []
    total = (
        await db.scalar(select(func.count()).select_from(User).where(*filters)) or 0
    )
    users = (
        await db.execute(
            select(User)
            .where(*filters)
            .order_by(User.created_at.desc(), User.id.desc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars()

    job_counts = dict(
        (
            await db.execute(
                select(Job.user_id, func.count()).group_by(Job.user_id)
            )
        ).all()
    )

    items = [
        _admin_user_out(user, job_counts.get(user.id, 0)) for user in users
    ]
    await db.commit()  # persist lazy reset kuota
    return AdminUserList(items=items, total=total)


@router.get(
    "/users/{user_id}",
    response_model=AdminUserOut,
    summary="Detail satu user (profil + kuota/kredit) — FR-13",
)
async def admin_user_detail(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> AdminUserOut:
    """Profil lengkap satu user — dipakai halaman detail admin
    (email, kuota, kredit, consent, jumlah riwayat job).
    """
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User tidak ditemukan"
        )
    job_count = (
        await db.scalar(
            select(func.count()).select_from(Job).where(Job.user_id == user.id)
        )
        or 0
    )
    out = _admin_user_out(user, job_count)
    await db.commit()  # persist lazy reset kuota
    return out


@router.get(
    "/users/{user_id}/transactions",
    response_model=AdminTransactionList,
    summary="Transaksi kredit satu user — FR-13",
)
async def admin_user_transactions(
    user_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> AdminTransactionList:
    """Riwayat pembelian kredit (FR-11) milik satu user — terbaru dulu.
    Dipakai halaman detail admin untuk audit transaksi.
    """
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User tidak ditemukan"
        )
    total = (
        await db.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.user_id == user.id)
        )
        or 0
    )
    rows = (
        await db.execute(
            select(Transaction)
            .where(Transaction.user_id == user.id)
            .order_by(Transaction.created_at.desc(), Transaction.id.desc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars()
    items = [
        AdminTransactionOut(
            id=t.id,
            order_id=t.order_id,
            provider=t.provider,
            package_slug=t.package_slug,
            amount_idr=t.amount_idr,
            credits=t.credits,
            status=t.status,
            created_at=t.created_at.isoformat() if t.created_at else None,
            paid_at=t.paid_at.isoformat() if t.paid_at else None,
        )
        for t in rows
    ]
    return AdminTransactionList(items=items, total=total)


@router.post(
    "/quota/reset",
    response_model=QuotaResetOut,
    summary="Reset kuota gratis user (alat admin)",
)
async def admin_quota_reset(
    body: QuotaResetRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> QuotaResetOut:
    """Reset pemakaian kuota gratis (FR-06) untuk satu user atau semua.

    Alat untuk pengelola/pengembang saat uji coba: set `used=0` dan tanggal
    reset ke hari ini (WIB) — tanpa mengubah limit dari env.
    """
    if not body.email and not body.all:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tentukan `email` (satu user) atau `all=true` (semua user)",
        )

    today = wib_today().isoformat()

    if body.email:
        user = await db.scalar(select(User).where(User.email == body.email))
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User tidak ditemukan: {body.email}",
            )
        user.free_daily_quota_used = 0
        user.free_quota_date = today
        await db.commit()
        return QuotaResetOut(reset=1, email=body.email)

    # Reset semua user (dev/uji coba) — lazy reset membuat used=0 aman.
    users = (await db.execute(select(User))).scalars()
    count = 0
    for user in users:
        user.free_daily_quota_used = 0
        user.free_quota_date = today
        count += 1
    await db.commit()
    return QuotaResetOut(reset=count, email=None)


@router.delete(
    "/jobs/{job_id}",
    summary="Hapus job + file-nya (alat admin)",
)
async def admin_delete_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> dict[str, object]:
    """Hapus permanen job (baris DB + file original & hasil di disk).

    File hanya dihapus bila berada di dalam upload_dir/result_dir (guard
    path traversal via `delete_if_inside`). Dipakai pengelola membersihkan
    data uji coba.
    """
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job tidak ditemukan"
        )

    files_deleted = 0
    if delete_if_inside(job.original_path, settings.upload_dir):
        files_deleted += 1
    if delete_if_inside(job.result_path, settings.result_dir):
        files_deleted += 1

    await db.delete(job)
    await db.commit()
    return {"deleted": True, "id": job_id, "files_deleted": files_deleted}
