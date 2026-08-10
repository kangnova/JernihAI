"""add admin_notes to jobs

Revision ID: 02904bc0ca94
Revises: 0001_initial
Create Date: 2026-08-11 04:32:27

CONTOH alur autogenerate (ADR-011): file ini DIHASILKAN oleh
`alembic revision --autogenerate -m "add admin_notes to jobs"` setelah
kolom `admin_notes` ditambahkan di model Job — diff bersih, tepat satu
perubahan (ADD COLUMN nullable tanpa server_default, jadi aman untuk
baris lama). Untuk migrasi nyata: review diff autogenerate, rapikan
marker bawaan, lalu uji upgrade/downgrade.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "02904bc0ca94"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs", sa.Column("admin_notes", sa.String(length=500), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("jobs", "admin_notes")
