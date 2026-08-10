"""Pembayaran (FR-11) — Midtrans Snap, provider-agnostic.

Modul ini hanya berisi logika MURNI (tanpa DB): paket, pembuatan token
Snap, dan verifikasi signature notifikasi. Mutasi DB (transaksi/saldo)
ada di route `app/api/routes/billing.py`.

Mode dev: tanpa `MIDTRANS_SERVER_KEY`/`CLIENT_KEY` (atau tanpa paket SDK
`midtransclient` terinstal), checkout mengembalikan token MOCK — alur
end-to-end tetap bisa diuji; webhook tetap menolak 403 tanpa key.
"""

import hashlib
import hmac
import logging
import uuid

from app.core.config import settings

logger = logging.getLogger(__name__)


def packages() -> dict[str, dict]:
    """Paket harga kredit (PRD §11) — key = slug, value {credits, price_idr}."""
    return settings.billing_packages


def get_package(slug: str) -> dict | None:
    return packages().get(slug)


def _order_id(user_id: str) -> str:
    """Order id unik: `<awal-user>-<uuid pendek>` (unik per user)."""
    return f"{user_id[:8]}-{uuid.uuid4().hex[:16]}"


def create_checkout(
    user_id: str, user_email: str | None, package_slug: str
) -> dict:
    """Buat order id + minta token Snap dari Midtrans.

    Return dict: {order_id, snap_token, redirect_url}. Tanpa konfigurasi
    gateway, snap_token = `mock-<order_id>` (dev).
    """
    pkg = get_package(package_slug)
    if pkg is None:
        raise ValueError(f"paket tidak dikenal: {package_slug}")

    order_id = _order_id(user_id)
    gross = int(pkg["price_idr"])
    name = settings.midtrans_item_name

    if not settings.midtrans_server_key:
        logger.info(
            "Checkout dev (tanpa MIDTRANS_SERVER_KEY) — token mock untuk %s",
            order_id,
        )
        return {
            "order_id": order_id,
            "snap_token": f"mock-{order_id}",
            "redirect_url": None,
        }

    try:
        import midtransclient  # type: ignore[import-not-found]
    except ImportError:
        logger.warning(
            "midtransclient tidak terinstal — token mock untuk %s", order_id
        )
        return {
            "order_id": order_id,
            "snap_token": f"mock-{order_id}",
            "redirect_url": None,
        }

    snap = midtransclient.Snap(
        is_production=settings.midtrans_is_production,
        server_key=settings.midtrans_server_key,
        client_key=settings.midtrans_client_key,
    )
    transaction = snap.create_transaction(
        {
            "transaction_details": {
                "order_id": order_id,
                "gross_amount": gross,
            },
            "item_details": [
                {
                    "id": package_slug,
                    "price": gross,
                    "quantity": 1,
                    "name": f"{name} ({pkg['credits']} kredit)",
                }
            ],
            "customer_details": {
                "first_name": user_email or "Pelanggan",
                "email": user_email,
            },
        }
    )
    return {
        "order_id": order_id,
        "snap_token": transaction.get("token"),
        "redirect_url": transaction.get("redirect_url"),
    }


def verify_notification_signature(payload: dict) -> bool:
    """Verifikasi signature SHA512 notifikasi Midtrans.

    Algoritma resmi: sha512(order_id + status_code + gross_amount +
    server_key) dibandingkan dengan `signature_key` di payload.
    Tanpa server key (dev) -> selalu salah (webhook 403).
    """
    server_key = settings.midtrans_server_key
    if not server_key:
        return False
    order_id = str(payload.get("order_id", ""))
    status_code = str(payload.get("status_code", ""))
    gross = str(payload.get("gross_amount", ""))
    received = str(payload.get("signature_key", ""))
    raw = f"{order_id}{status_code}{gross}{server_key}"
    expected = hashlib.sha512(raw.encode("utf-8")).hexdigest()
    return hmac.compare_digest(expected, received)


def is_paid_status(payload: dict) -> bool:
    """Apakah status notifikasi berarti pembayaran sukses (kredit cair)?"""
    txn_status = payload.get("transaction_status")
    fraud = payload.get("fraud_status")
    return bool(
        txn_status in ("capture", "settlement")
        or txn_status == "accept"  # bank transfer settlement
        or (txn_status == "pending" and fraud == "accept")
    )


def is_failed_status(payload: dict) -> bool:
    txn_status = payload.get("transaction_status")
    return txn_status in ("cancel", "deny", "expire", "failure")
