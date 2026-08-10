"""initial: tabel users, jobs, transactions + konvergensi DB lama

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-11

Migrasi pertama — skema identik dengan model ORM (app/models/), mencakup
tabel `users` (FR-01), `jobs` (FR-02), `transactions` (FR-11) beserta
semua kolom yang ditambahkan lintas FR (FR-06 kuota, FR-07 retensi &
consent, FR-08 face_enhance, FR-09 denoise/color_enhance, FR-11 kredit).

KENAPA ADA GUARD TABEL + BACKFILL KOLOM? Sebelum Alembic (ADR-011),
skema dibuat `Base.metadata.create_all` di runtime — DB yang sudah ada
(mis. VPS lama) punya tabel tanpa `alembic_version`. Upgrade head harus
berjalan mulus di DB FRESH maupun DB LAMA:

- Tabel yang sudah ada dilewati (guard), yang belum ada dibuat
  (mis. `transactions`).
- Kolom FR-07/08/09/11 yang belum ada di tabel lama DITAMBAHKAN
  (backfill) dengan server_default supaya baris lama valid.

Hasilnya: deploy ke VPS TIDAK perlu `docker compose down -v`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("users", "jobs", "transactions")

# Kolom yang berpotensi hilang di DB lama (era create_all versi lama) —
# TIDAK termasuk kolom yang ada sejak FR-01/FR-02 (id, email, password,
# nama, path, dsb.). server_default hanya untuk backfill baris lama;
# model ORM memakai default sisi Python (skema create_table di bawah
# identik dengan model: tanpa server_default).
_USERS_BACKFILL = {
    "provider": sa.Column("provider", sa.String(20), nullable=False, server_default="local"),
    "is_active": sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
    "free_daily_quota_used": sa.Column("free_daily_quota_used", sa.Integer(), nullable=False, server_default="0"),
    "free_quota_date": sa.Column("free_quota_date", sa.String(10), nullable=False, server_default="1970-01-01"),
    "credit_balance": sa.Column("credit_balance", sa.Integer(), nullable=False, server_default="0"),
    "privacy_consent_at": sa.Column("privacy_consent_at", sa.DateTime(timezone=True), nullable=True),
}

_JOBS_BACKFILL = {
    "user_id": sa.Column("user_id", sa.String(36), nullable=False, server_default=""),
    "status": sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
    "scale": sa.Column("scale", sa.Integer(), nullable=False, server_default="2"),
    "output_format": sa.Column("output_format", sa.String(10), nullable=False, server_default="webp"),
    "face_enhance": sa.Column("face_enhance", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    "denoise": sa.Column("denoise", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    "color_enhance": sa.Column("color_enhance", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    "uses_credit": sa.Column("uses_credit", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    "original_name": sa.Column("original_name", sa.String(255), nullable=False, server_default=""),
    "original_path": sa.Column("original_path", sa.String(500), nullable=False, server_default=""),
    "result_path": sa.Column("result_path", sa.String(500), nullable=True),
    "original_deleted_at": sa.Column("original_deleted_at", sa.DateTime(timezone=True), nullable=True),
    "result_deleted_at": sa.Column("result_deleted_at", sa.DateTime(timezone=True), nullable=True),
    "error": sa.Column("error", sa.String(500), nullable=True),
    "finished_at": sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
}


def _inspector():
    """Inspector DB; None di mode offline (--sql) — guard jadi no-op."""
    if context.is_offline_mode():
        return None
    return sa.inspect(op.get_bind())


def _existing_tables() -> set[str]:
    insp = _inspector()
    return set(insp.get_table_names()) if insp else set()


def _existing_indexes(table: str) -> set[str]:
    insp = _inspector()
    return {idx["name"] for idx in insp.get_indexes(table)} if insp else set()


def _existing_columns(table: str) -> set[str]:
    insp = _inspector()
    return {c["name"] for c in insp.get_columns(table)} if insp else set()


def _backfill_columns(table: str, columns: dict[str, sa.Column]) -> None:
    """Tambahkan kolom yang belum ada (guard per kolom) + server_default.

    Dipakai HANYA untuk DB lama yang dibuat create_all versi sebelumnya —
    kolom baru ditambahkan tanpa menghapus data, default mengisi baris lama.
    """
    existing = _existing_columns(table)
    for name, column in columns.items():
        if name not in existing:
            op.add_column(table, column)


def _create_index_if_missing(
    index_name: str, table: str, columns: list[str], *, unique: bool = False
) -> None:
    """Buat index hanya bila belum ada (aman utk DB lama parsial)."""
    if index_name in _existing_indexes(table):
        return
    op.create_index(index_name, table, columns, unique=unique)


def upgrade() -> None:
    existing = _existing_tables()

    if "users" not in existing:
        op.create_table(
            "users",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=True),
            sa.Column("password_hash", sa.String(length=255), nullable=True),
            sa.Column("provider", sa.String(length=20), nullable=False),
            sa.Column("provider_sub", sa.String(length=255), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("free_daily_quota_used", sa.Integer(), nullable=False),
            sa.Column("free_quota_date", sa.String(length=10), nullable=False),
            sa.Column("credit_balance", sa.Integer(), nullable=False),
            sa.Column("privacy_consent_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    else:
        # DB lama: lengkapi kolom FR-06/07/11 yang mungkin belum ada.
        _backfill_columns("users", _USERS_BACKFILL)
    # email unik (auth); provider di-index utk filter OAuth.
    _create_index_if_missing("ix_users_email", "users", ["email"], unique=True)
    _create_index_if_missing("ix_users_provider", "users", ["provider"])

    if "jobs" not in existing:
        op.create_table(
            "jobs",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("scale", sa.Integer(), nullable=False),
            sa.Column("output_format", sa.String(length=10), nullable=False),
            sa.Column("face_enhance", sa.Boolean(), nullable=False),
            sa.Column("denoise", sa.Boolean(), nullable=False),
            sa.Column("color_enhance", sa.Boolean(), nullable=False),
            sa.Column("uses_credit", sa.Boolean(), nullable=False),
            sa.Column("original_name", sa.String(length=255), nullable=False),
            sa.Column("original_path", sa.String(length=500), nullable=False),
            sa.Column("result_path", sa.String(length=500), nullable=True),
            sa.Column("original_deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("result_deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error", sa.String(length=500), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    else:
        # DB lama: lengkapi kolom FR-07/08/09/11 yang mungkin belum ada.
        _backfill_columns("jobs", _JOBS_BACKFILL)
    _create_index_if_missing("ix_jobs_user_id", "jobs", ["user_id"])
    _create_index_if_missing("ix_jobs_status", "jobs", ["status"])

    if "transactions" not in existing:
        op.create_table(
            "transactions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("order_id", sa.String(length=64), nullable=False),
            sa.Column("provider", sa.String(length=20), nullable=False),
            sa.Column("provider_txn_id", sa.String(length=64), nullable=True),
            sa.Column("package_slug", sa.String(length=50), nullable=False),
            sa.Column("amount_idr", sa.Integer(), nullable=False),
            sa.Column("credits", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    # order_id UNIQUE = kunci idempotensi webhook FR-11 (anti double-credit).
    _create_index_if_missing("ix_transactions_order_id", "transactions", ["order_id"], unique=True)
    _create_index_if_missing("ix_transactions_user_id", "transactions", ["user_id"])
    _create_index_if_missing("ix_transactions_provider_txn_id", "transactions", ["provider_txn_id"])
    _create_index_if_missing("ix_transactions_status", "transactions", ["status"])


def downgrade() -> None:
    """Drop hanya tabel yang ADA (safe bila sebagian belum ada)."""
    existing = _existing_tables()
    for table in reversed(_TABLES):
        if table in existing:
            op.drop_table(table)
