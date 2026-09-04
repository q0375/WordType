"""建库 + 种子（首个 admin D15）。词库一律由用户导入，不再内置演示词。"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..core.security import hash_password
from ..models import DailySetting, User, WordBook
from .engine import get_sessionmaker

logger = logging.getLogger("wordtype.init")



async def init_db() -> None:
    from ..models import Base

    from .engine import engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # 轻量迁移：create_all 不会给已存在的表补列，这里手动补
        from sqlalchemy import text

        cols = (await conn.execute(text("PRAGMA table_info(daily_setting)"))).fetchall()
        col_names = {c[1] for c in cols}
        if "game_key_sound" not in col_names:
            await conn.execute(text("ALTER TABLE daily_setting ADD COLUMN game_key_sound INTEGER NOT NULL DEFAULT 1"))
            logger.info("migrated daily_setting: added game_key_sound")

    s = get_settings()
    async with get_sessionmaker()() as db:
        admin = (
            await db.execute(select(User).where(User.role == "admin"))
        ).scalars().first()
        if admin is None:
            db.add(
                User(username=s.admin_username, password_hash=hash_password(s.admin_password), role="admin")
            )
            logger.info("seeded admin user %s", s.admin_username)

        # 演示词库种子已移除：词库一律由用户导入（支持自选单元词数、随机分配）
        # 所有用户确保有设置行
        users = (await db.execute(select(User))).scalars().all()
        for u in users:
            ds = (await db.execute(select(DailySetting).where(DailySetting.user_id == u.id))).scalar_one_or_none()
            if ds is None:
                db.add(DailySetting(user_id=u.id))
        await db.commit()
