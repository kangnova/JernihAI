"""Environment Alembic — async (postgresql+asyncpg / sqlite+aiosqlite).

Mengikuti pola resmi SQLAlchemy untuk engine async. URL database diambil
dari konfigurasi aplikasi (`settings.database_url` ← env `DATABASE_URL`)
supaya satu sumber kebenaran dengan engine runtime — kecuali `alembic.ini`
mengisi `sqlalchemy.url` (dipakai test dengan SQLite).

Semua model di-import agar `Base.metadata` lengkap (autogenerate & create
table konsisten dengan ORM).
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app import models  # noqa: F401 — daftarkan SEMUA model ke metadata
from app.core.config import settings
from app.models.base import Base
from migrations.lock import acquire as acquire_migration_lock
from migrations.lock import release as release_migration_lock

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Fallback: alembic.ini kosong -> pakai URL aplikasi (env DATABASE_URL).
config.set_main_option(
    "sqlalchemy.url", config.get_main_option("sqlalchemy.url") or settings.database_url
)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Jalankan migrasi dalam mode 'offline' (SQL string, tanpa DB)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        # Fase 3 (multi-instance): serialisasi `upgrade head` antar replica
        # (migrations/lock.py). Lock session-level di-commit agar migrasi
        # mulai dari transaksi bersih; lock bertahan sampai release.
        await acquire_migration_lock(connection)
        await connection.commit()
        try:
            await connection.run_sync(do_run_migrations)
        finally:
            await release_migration_lock(connection)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
