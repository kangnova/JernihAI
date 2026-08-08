"""SQLAlchemy async engine & session (PostgreSQL; SQLite hanya untuk dev).

Catatan: engine dibuat secara lazy — koneksi tidak dibuka sampai query
pertama, sehingga health check tidak bergantung pada ketersediaan DB.

PENTING (pool=NullPool): task Celery menjalankan `asyncio.run(process_job)`
yang membuat event loop BARU per job. Pool koneksi default menyimpan
koneksi asyncpg terikat ke loop pertama → job kedua crash
("attached to a different loop"). NullPool membuat koneksi segar per sesi
di loop berjalan, jadi aman untuk pola worker + asyncio.run. Biaya
koneksi per-sesi tidak masalah pada skala MVP; bila nanti worker
menggunakan loop persisten, kembalikan ke pool default untuk efisiensi.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True, poolclass=NullPool)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session
