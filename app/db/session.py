from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.config import settings

# Async Engine for FastAPI API endpoints
engine = create_async_engine(
    settings.get_database_url(),
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()

# Sync engine used for Alembic and CLI tools if needed
sync_engine = create_engine(
    settings.get_sync_database_url(),
    echo=False,
    pool_pre_ping=True,
)
sync_session_factory = sessionmaker(bind=sync_engine, expire_on_commit=False)
