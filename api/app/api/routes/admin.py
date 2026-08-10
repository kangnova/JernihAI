"""Admin dashboard API (FR-13) — monitoring user, job, revenue.

Hanya untuk email yang terdaftar di `ADMIN_EMAILS` (lihat
`app.api.deps.require_admin`). Revenue diisi FR-11 (pembayaran); saat ini
mengembalikan nol.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.quota import quota_limit
from app.db.session import get_db
from app.models.job import Job, JobStatus
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
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> AdminJobList:
    """Daftar job semua user (terbaru dulu) — untuk monitoring operasional."""
    total = await db.scalar(select(func.count()).select_from(Job)) or 0
    rows = await db.execute(
        select(Job, User.email)
        .join(User, Job.user_id == User.id, isouter=True)
        .order_by(Job.created_at.desc(), Job.id.desc())
        .offset(offset)
        .limit(limit)
    )
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
