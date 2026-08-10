"""Pembayaran kredit (FR-11) — Midtrans Snap.

Alur: user pilih paket -> `POST /billing/checkout` membuat Transaction
(pending) + token Snap -> frontend membuka Snap -> Midtrans mengirim
notifikasi ke `/billing/webhook` -> status paid -> saldo kredit user
ditambah. Webhook idempoten: hanya transisi pending -> paid sekali.

`POST /billing/webhook` TIDAK memakai auth user — keamanannya adalah
verifikasi signature SHA512 (app/core/billing.py).
"""

import logging
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core import billing
from app.db.session import get_db
from app.models.transaction import Transaction, TransactionStatus
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


class PackageOut(BaseModel):
    slug: str
    credits: int
    price_idr: int


class PackagesOut(BaseModel):
    credit_balance: int
    packages: list[PackageOut]


class CheckoutRequest(BaseModel):
    package_slug: str = Field(min_length=1, max_length=50)


class CheckoutOut(BaseModel):
    order_id: str
    snap_token: str
    redirect_url: str | None = None
    credits: int
    amount_idr: int


class TransactionOut(BaseModel):
    id: str
    order_id: str
    package_slug: str
    amount_idr: int
    credits: int
    status: str
    created_at: str | None = None
    paid_at: str | None = None


class TransactionsOut(BaseModel):
    items: list[TransactionOut]
    total: int


@router.get(
    "/packages",
    response_model=PackagesOut,
    summary="Paket kredit + saldo (FR-11)",
)
async def get_packages(
    current_user: User = Depends(get_current_user),
) -> PackagesOut:
    return PackagesOut(
        credit_balance=current_user.credit_balance or 0,
        packages=[
            PackageOut(slug=slug, credits=p["credits"], price_idr=p["price_idr"])
            for slug, p in billing.packages().items()
        ],
    )


@router.post(
    "/checkout",
    response_model=CheckoutOut,
    status_code=status.HTTP_201_CREATED,
    summary="Buat order + token Snap (FR-11)",
)
async def checkout(
    body: CheckoutRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CheckoutOut:
    pkg = billing.get_package(body.package_slug)
    if pkg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Paket tidak ditemukan: {body.package_slug}",
        )

    try:
        checkout_info = billing.create_checkout(
            current_user.id, current_user.email, body.package_slug
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    txn = Transaction(
        id=str(uuid4()),
        user_id=current_user.id,
        order_id=checkout_info["order_id"],
        provider="midtrans",
        package_slug=body.package_slug,
        amount_idr=pkg["price_idr"],
        credits=pkg["credits"],
        status=TransactionStatus.PENDING.value,
    )
    db.add(txn)
    await db.commit()

    return CheckoutOut(
        order_id=txn.order_id,
        snap_token=checkout_info["snap_token"],
        redirect_url=checkout_info["redirect_url"],
        credits=txn.credits,
        amount_idr=txn.amount_idr,
    )


@router.get(
    "/transactions",
    response_model=TransactionsOut,
    summary="Riwayat transaksi user (FR-11)",
)
async def list_transactions(
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TransactionsOut:
    total = len(
        (
            await db.execute(
                select(Transaction).where(Transaction.user_id == current_user.id)
            )
        ).scalars().all()
    )
    rows = (
        await db.execute(
            select(Transaction)
            .where(Transaction.user_id == current_user.id)
            .order_by(Transaction.created_at.desc(), Transaction.id.desc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars()

    items = [
        TransactionOut(
            id=t.id,
            order_id=t.order_id,
            package_slug=t.package_slug,
            amount_idr=t.amount_idr,
            credits=t.credits,
            status=t.status,
            created_at=t.created_at.isoformat() if t.created_at else None,
            paid_at=t.paid_at.isoformat() if t.paid_at else None,
        )
        for t in rows
    ]
    return TransactionsOut(items=items, total=total)


@router.post(
    "/webhook",
    summary="Notifikasi pembayaran Midtrans (FR-11)",
)
async def midtrans_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Terima notifikasi Midtrans, verifikasi signature, cairkan kredit.

    Idempoten: order yang sudah `paid` tidak diproses ulang (webhook
    duplikat/tunda tidak menggandakan saldo). Balas selalu 200 untuk
    event yang sah (Midtrans mengulang pengiriman bila bukan 2xx).
    """
    payload = await request.json()
    if not billing.verify_notification_signature(payload):
        logger.warning("Webhook pembayaran dengan signature tidak valid ditolak")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Signature tidak valid",
        )

    order_id = str(payload.get("order_id", ""))
    if not order_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="order_id kosong"
        )

    result = await db.execute(
        select(Transaction).where(Transaction.order_id == order_id)
    )
    txn = result.scalar_one_or_none()
    if txn is None:
        logger.warning("Webhook untuk order tidak dikenal: %s", order_id)
        return {"status": "ignored", "reason": "order tidak dikenal"}

    # Idempotensi: hanya proses transisi pending -> final.
    if txn.status != TransactionStatus.PENDING.value:
        return {"status": "ignored", "reason": f"sudah {txn.status}"}

    if billing.is_paid_status(payload):
        user = await db.get(User, txn.user_id)
        if user is None:
            return {"status": "ignored", "reason": "user tidak ada"}
        # Idempotensi ATOMIK (race-safe): Midtrans mengirim ulang webhook
        # (retry/duplikat) — hanya SATU notifikasi yang berhasil melakukan
        # transisi pending -> paid; notifikasi lain rowcount=0 (dilewati),
        # jadi saldo tidak pernah digandakan.
        result = await db.execute(
            update(Transaction)
            .where(
                Transaction.id == txn.id,
                Transaction.status == TransactionStatus.PENDING.value,
            )
            .values(
                status=TransactionStatus.PAID.value,
                paid_at=datetime.now(UTC),
                provider_txn_id=str(payload.get("transaction_id") or ""),
            )
        )
        if result.rowcount == 0:
            return {"status": "ignored", "reason": "sudah diproses"}
        user.credit_balance = (user.credit_balance or 0) + txn.credits
        await db.commit()
        logger.info(
            "Kredit %d cair untuk user %s (order %s)",
            txn.credits,
            txn.user_id,
            order_id,
        )
        return {"status": "paid", "credits": str(txn.credits)}

    if billing.is_failed_status(payload):
        # Status final gagal: expire/cancel/deny/failure. `expire` dipetakan
        # ke status EXPIRED agar label UI ("Kedaluwarsa") tepat.
        final = (
            TransactionStatus.EXPIRED.value
            if payload.get("transaction_status") == "expire"
            else TransactionStatus.FAILED.value
        )
        txn.status = final
        await db.commit()
        return {"status": final}

    # Pending/challenge: belum final, biarkan.
    return {"status": "pending"}
