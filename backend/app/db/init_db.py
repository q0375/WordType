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
        if "advice_engine" not in col_names:
            await conn.execute(text("ALTER TABLE daily_setting ADD COLUMN advice_engine TEXT NOT NULL DEFAULT 'rule'"))
            logger.info("migrated daily_setting: added advice_engine")
        if "review_show_meaning" not in col_names:
            await conn.execute(text("ALTER TABLE daily_setting ADD COLUMN review_show_meaning INTEGER NOT NULL DEFAULT 1"))
            logger.info("migrated daily_setting: added review_show_meaning")
        for col, ddl in (
            ("game_fall_mode", "INTEGER NOT NULL DEFAULT 1"),
            ("game_fall_base", "INTEGER NOT NULL DEFAULT 8"),
            ("game_fall_per_char", "INTEGER NOT NULL DEFAULT 6"),
        ):
            if col not in col_names:
                await conn.execute(text(f"ALTER TABLE daily_setting ADD COLUMN {col} {ddl}"))
                logger.info(f"migrated daily_setting: added {col}")

        acols = {c[1] for c in (await conn.execute(text("PRAGMA table_info(advice_cache)"))).fetchall()}
        if acols and "note" not in acols:
            await conn.execute(text("ALTER TABLE advice_cache ADD COLUMN note TEXT NOT NULL DEFAULT ''"))
            logger.info("migrated advice_cache: added note")

        # 班级 / 考核卷（v3.5）：user.class_id、exam_paper.template_id/source
        ucols = {c[1] for c in (await conn.execute(text("PRAGMA table_info(user)"))).fetchall()}
        if ucols and "class_id" not in ucols:
            await conn.execute(text("ALTER TABLE user ADD COLUMN class_id INTEGER"))
            logger.info("migrated user: added class_id")
        if ucols and "nickname" not in ucols:
            await conn.execute(text("ALTER TABLE user ADD COLUMN nickname TEXT"))
            logger.info("migrated user: added nickname")
        if ucols and "last_seen_at" not in ucols:
            await conn.execute(text("ALTER TABLE user ADD COLUMN last_seen_at TEXT"))
            logger.info("migrated user: added last_seen_at")
        ecols = {c[1] for c in (await conn.execute(text("PRAGMA table_info(exam_paper)"))).fetchall()}
        if ecols:
            if "template_id" not in ecols:
                await conn.execute(text("ALTER TABLE exam_paper ADD COLUMN template_id INTEGER"))
                logger.info("migrated exam_paper: added template_id")
            if "source" not in ecols:
                await conn.execute(text("ALTER TABLE exam_paper ADD COLUMN source TEXT NOT NULL DEFAULT 'self'"))
                logger.info("migrated exam_paper: added source")
        acols2 = {c[1] for c in (await conn.execute(text("PRAGMA table_info(class_paper_assign)"))).fetchall()}
        if acols2 and "max_attempts" not in acols2:
            await conn.execute(text("ALTER TABLE class_paper_assign ADD COLUMN max_attempts INTEGER"))
            logger.info("migrated class_paper_assign: added max_attempts")
        wcols = {c[1] for c in (await conn.execute(text("PRAGMA table_info(word)"))).fetchall()}
        if wcols and "time_limit_s" not in wcols:
            await conn.execute(text("ALTER TABLE word ADD COLUMN time_limit_s INTEGER"))
            logger.info("migrated word: added time_limit_s")

        # 排行榜（v3.6）：对局与成绩记录词库/章节范围，供「公共词库排行榜」按 词库+章节+模式+难度 分组
        for tbl in ("game_session", "game_record"):
            gcols = {c[1] for c in (await conn.execute(text(f"PRAGMA table_info({tbl})"))).fetchall()}
            if not gcols:
                continue
            for col in ("book_id", "chapter_id"):
                if col not in gcols:
                    await conn.execute(text(f"ALTER TABLE {tbl} ADD COLUMN {col} INTEGER"))
                    logger.info(f"migrated {tbl}: added {col}")
            # 章节集合分组键（2026-09-23）：NULL=整本；历史单章局回填为单元素键，保持原榜可见
            if "chapter_key" not in gcols:
                await conn.execute(text(f"ALTER TABLE {tbl} ADD COLUMN chapter_key TEXT"))
                if tbl == "game_record":
                    await conn.execute(
                        text("UPDATE game_record SET chapter_key = CAST(chapter_id AS TEXT) "
                             "WHERE chapter_id IS NOT NULL AND chapter_key IS NULL")
                    )
                logger.info(f"migrated {tbl}: added chapter_key")

    s = get_settings()
    async with get_sessionmaker()() as db:
        admin = (
            await db.execute(select(User).where(User.role == "admin"))
        ).scalars().first()
        if admin is None:
            db.add(
                User(username=s.admin_username, password_hash=await hash_password(s.admin_password), role="admin")
            )
            logger.info("seeded admin user %s", s.admin_username)

        # 演示词库种子已移除：词库一律由用户导入（支持自选单元词数、随机分配）
        # 默认班级：固定存在，未分配用户全部归入
        from ..models import StudyClass

        default_cls = (await db.execute(select(StudyClass).where(StudyClass.is_default == 1))).scalars().first()
        if default_cls is None:
            default_cls = StudyClass(name="默认班级", is_default=1)
            db.add(default_cls)
            await db.flush()
            logger.info("seeded default class #%s", default_cls.id)
        # 所有用户确保有设置行；未分配班级的用户归默认班级
        users = (await db.execute(select(User))).scalars().all()
        for u in users:
            ds = (await db.execute(select(DailySetting).where(DailySetting.user_id == u.id))).scalar_one_or_none()
            if ds is None:
                db.add(DailySetting(user_id=u.id))
            if u.class_id is None:
                u.class_id = default_cls.id
        await db.commit()
