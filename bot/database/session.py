from __future__ import annotations

from collections.abc import AsyncIterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from bot.config import Settings
from bot.database.models import Base


class Database:
    def __init__(self, settings: Settings) -> None:
        database_url = settings.database_url
        connect_args: dict[str, object] = {}
        parts = urlsplit(database_url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        if "sslmode" in query:
            ssl_mode = query.pop("sslmode")
            internal_host = parts.hostname in {"helium", "localhost", "127.0.0.1"}
            connect_args["ssl"] = False if internal_host or ssl_mode == "disable" else True
            database_url = urlunsplit(
                (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
            )
        # channel_binding is a libpq/psycopg option and is not accepted by
        # asyncpg. Remove it before SQLAlchemy forwards URL query parameters
        # to asyncpg.connect().
        query.pop("channel_binding", None)
        database_url = urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )
        self.engine: AsyncEngine = create_async_engine(
            database_url,
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_size=5,
            max_overflow=10,
            connect_args=connect_args,
        )
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)

    async def init(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def ping(self) -> bool:
        try:
            async with self.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            yield session

    async def close(self) -> None:
        await self.engine.dispose()
