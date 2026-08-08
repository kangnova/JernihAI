"""SQLAlchemy async engine & session (PostgreSQL; SQLite hanya untuk dev).

Catatan: engine dibuat secara lazy — koneksi tidak dibuka sampai query
pertama, sehingga health check tidak bergantung pada ketersediaan DB.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session
