"""Advisory lock migrasi (Fase 3 multi-instance) — modul MANDIRI.

env.py tidak bisa di-import langsung di luar run alembic (`context.config`
hanya tersedia saat alembic mengeksekusi env.py), jadi helper lock ditaruh
di sini agar bisa diuji unit. `prepend_sys_path = .` di alembic.ini
memastikan `migrations.lock` ter-import dari env.py.

Masalah yang diselesaikan: dua replica `api` yang start bersamaan
(`docker compose up -d --scale api=2`) sama-sama menjalankan
`alembic upgrade head`; tanpa lock salah satu bisa crash di tengah upgrade.
"""

from sqlalchemy import text

# int64 acak — kunci serialisasi migrasi (bukan kunci lain di DB).
MIGRATION_LOCK_KEY = 694201337


async def acquire(connection) -> None:
    """`pg_advisory_lock` SESSION-LEVEL: bertahan meski transaksi SELECT-nya
    di-commit; dilepas eksplisit (`release`) atau otomatis saat koneksi
    ditutup. SQLite (test/dev) tidak punya advisory lock — dilewati.

    `connection` adalah AsyncConnection — `execute` harus di-await.
    """
    if connection.dialect.name == "postgresql":
        await connection.execute(
            text("SELECT pg_advisory_lock(:k)"), {"k": MIGRATION_LOCK_KEY}
        )


async def release(connection) -> None:
    if connection.dialect.name == "postgresql":
        await connection.execute(
            text("SELECT pg_advisory_unlock(:k)"), {"k": MIGRATION_LOCK_KEY}
        )
