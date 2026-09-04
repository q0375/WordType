"""async engine + PRAGMA（WAL/busy_timeout/foreign_keys）+ 会话 + 写重试。"""

import asyncio
import functools
import logging
from datetime import datetime

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..core.config import get_settings
from ..core.errors import AppError

logger = logging.getLogger("wordtype.db")

engine = create_async_engine(
    get_settings().database_url,
    echo=False,
    pool_pre_ping=True,
)

_sessionmaker = async_sessionmaker(engine, expire_on_commit=False)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _):  # noqa: ANN001
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA temp_store=MEMORY")
    cursor.close()


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return _sessionmaker


async def get_db() -> AsyncSession:  # FastAPI 依赖
    async with _sessionmaker() as session:
        yield session


def write_retry(fn):
    """SQLite 写重试装饰器：database is locked → 指数退避 ×3 → 503 DB_BUSY。"""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        delay = 0.1
        for attempt in range(3):
            try:
                return await fn(*args, **kwargs)
            except AppError:
                raise
            except Exception as e:  # noqa: BLE001
                if "database is locked" not in str(e).lower() or attempt == 2:
                    if "database is locked" in str(e).lower():
                        raise AppError("DB_BUSY", "数据库繁忙，请稍后重试") from e
                    raise
                logger.warning("db locked, retry %d: %s", attempt + 1, e)
                await asyncio.sleep(delay)
                delay *= 2

    return wrapper


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")
