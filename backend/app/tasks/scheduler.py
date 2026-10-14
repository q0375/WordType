"""定时任务（APScheduler 进程内单实例）：02:00 备份 / 03:00 清理 / 04:00 LetterStat 聚合对账。

另含 03:30 注销账号物理清理（is_deleted=1 且超过 30 天）。
"""

import json
import logging
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import select, text, update

from ..core.config import get_settings
from ..db.engine import get_sessionmaker
from ..models import ExamPaper, GameSession, IdempotencyRecord, ImportJob, LetterStat, ReviewQueue, AdviceCache, TypingRecord, User

logger = logging.getLogger("wordtype.tasks")


async def job_backup() -> None:
    s = get_settings()
    ts = datetime.now().strftime("%Y%m%d")
    target = s.backup_dir / f"wordtype_{ts}.db"
    db_path = s.data_dir / "wordtype.db"
    try:
        raw = sqlite3.connect(db_path)
        dest = sqlite3.connect(target)
        with dest:
            raw.backup(dest)
        dest.close()
        raw.close()
        # 保留 30 份
        backups = sorted(s.backup_dir.glob("wordtype_*.db"))
        for old in backups[:-30]:
            old.unlink(missing_ok=True)
        logger.info("backup done: %s", target)
    except Exception as e:  # noqa: BLE001
        logger.error("backup failed: %s", e)


# 按 user_id 归属的表（用于清理孤儿行：任何硬删用户而漏清依赖数据的路径都会留下这些行）
USER_SCOPED_TABLES = (
    "daily_activity",
    "user_word_stat",
    "letter_stat",
    "typing_record",
    "game_record",
    "exam_record",
    "exam_paper",
    "wrong_book_item",
    "study_progress",
    "review_queue",
    "advice_cache",
    "daily_setting",
    "user_ai_config",
    "game_session",
    "import_job",
    "idempotency_record",
)


async def job_cleanup() -> None:
    async with get_sessionmaker()() as db:
        try:
            await db.execute(text("DELETE FROM idempotency_record WHERE created_at < :t"), {"t": (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")})
            await db.execute(text("DELETE FROM review_queue WHERE date < :d"), {"d": (date.today() - timedelta(days=7)).isoformat()})
            await db.execute(text("DELETE FROM advice_cache WHERE date < :d"), {"d": date.today().isoformat()})
            # 过期未结算 game session → void；过期未交卷 paper → void
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            await db.execute(update(GameSession).where(GameSession.status == "active", GameSession.expires_at < now).values(status="void"))
            await db.execute(update(ExamPaper).where(ExamPaper.status == "active", ExamPaper.expires_at < now).values(status="void"))
            await db.execute(text("DELETE FROM import_job WHERE expires_at < :t"), {"t": (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")})
            # 孤儿行清理：SQLite 会复用被删除用户的 id，残留行会被新用户「继承」（热力图/打卡/统计串号）
            for tbl in USER_SCOPED_TABLES:
                await db.execute(text(f"DELETE FROM {tbl} WHERE user_id NOT IN (SELECT id FROM user)"))
            await db.commit()
            logger.info("cleanup done")
        except Exception as e:  # noqa: BLE001
            await db.rollback()
            logger.error("cleanup failed: %s", e)


async def job_letter_stat() -> None:
    """T+1 水位线聚合（DBD §4.6）：typing_record.detail_json → letter_stat。"""
    async with get_sessionmaker()() as db:
        try:
            from ..models import SystemSetting

            row = (await db.execute(select(SystemSetting).where(SystemSetting.key == "letterstat_watermark"))).scalar_one_or_none()
            watermark = int(json.loads(row.value_json)) if row else 0
            records = (
                await db.execute(
                    select(TypingRecord).where(TypingRecord.id > watermark, TypingRecord.detail_json.is_not(None)).order_by(TypingRecord.id).limit(5000)
                )
            ).scalars().all()

            for r in records:
                try:
                    detail = json.loads(r.detail_json)
                except json.JSONDecodeError:
                    continue
                keys = detail.get("keys") or []
                prev_t = None
                for i, k in enumerate(keys):
                    expected = (k.get("expected") or "").lower()
                    ok = bool(k.get("ok"))
                    t = k.get("t")
                    for letter in {expected, expected + ((keys[i + 1].get("expected") or "").lower() if i + 1 < len(keys) else "")}:
                        if not letter:
                            continue
                        st = (await db.execute(select(LetterStat).where(LetterStat.user_id == r.user_id, LetterStat.letter == letter))).scalar_one_or_none()
                        if st is None:
                            st = LetterStat(user_id=r.user_id, letter=letter, avg_delay_ms=0, total_count=0, error_count=0)
                            db.add(st)
                        total = st.total_count
                        delay = (t - prev_t) if (prev_t is not None and t is not None) else 0
                        st.avg_delay_ms = (st.avg_delay_ms * total + max(0, delay)) / (total + 1)
                        st.total_count = total + 1
                        if not ok:
                            st.error_count += 1
                    prev_t = t

            # 水位线：整批处理完后 upsert 一次（必须在 for 循环外，否则同一主键重复 add → UNIQUE 冲突）
            if records:
                from ..models import SystemSetting as SS

                max_id = max(r.id for r in records)
                if row is None:
                    db.add(SS(key="letterstat_watermark", value_json=str(max_id), updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                else:
                    row.value_json = str(max_id)
            await db.commit()
            logger.info("letter_stat aggregated %d records", len(records))
        except Exception as e:  # noqa: BLE001
            await db.rollback()
            logger.error("letter_stat failed: %s", e)


async def job_purge_deactivated() -> None:
    """注销账号物理清理：is_deleted=1 且 deleted_at 超过 30 天 → 永久删除其全部数据。

    与个人中心文案承诺一致（「注销为软删除，30 天后定时任务物理清理（含私有词库与全部统计）」），
    这 30 天同时是管理员的恢复窗口。

    注意删除顺序（PRAGMA foreign_keys=ON，部分外键无级联）：
    word.chapter_id / typing_record.word_id / review_queue.word_id /
    wrong_book_item.word_id / user_word_stat.word_id 都是 NO ACTION，
    必须先清引用行、再删 word，最后才删 user（删 user 会级联到 word_book→chapter）。
    """
    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    async with get_sessionmaker()() as db:
        try:
            uids = (
                await db.execute(
                    select(User.id).where(User.is_deleted == 1, User.deleted_at.is_not(None), User.deleted_at < cutoff)
                )
            ).scalars().all()
            purged = 0
            for uid in uids:
                # 该用户私有词库下的全部单词（用于清理跨用户引用）
                word_sel = "SELECT id FROM word WHERE book_id IN (SELECT id FROM word_book WHERE owner_id = :u)"
                # ① 清掉对「待删单词」的所有引用（不限于注销用户本人，防跨用户残留引用阻塞删除）
                for tbl in ("user_word_stat", "typing_record", "review_queue", "wrong_book_item"):
                    await db.execute(text(f"DELETE FROM {tbl} WHERE word_id IN ({word_sel})"), {"u": uid})
                # ② 该用户自身的统计/记录（其中部分表 word_id 无级联）
                for tbl in ("user_word_stat", "typing_record", "review_queue", "wrong_book_item"):
                    await db.execute(text(f"DELETE FROM {tbl} WHERE user_id = :u"), {"u": uid})
                # ③ 私有词库的单词 → 章节 → 词库
                await db.execute(text("DELETE FROM word WHERE book_id IN (SELECT id FROM word_book WHERE owner_id = :u)"), {"u": uid})
                await db.execute(text("DELETE FROM chapter WHERE book_id IN (SELECT id FROM word_book WHERE owner_id = :u)"), {"u": uid})
                await db.execute(text("DELETE FROM word_book WHERE owner_id = :u"), {"u": uid})
                # ④ 无外键的孤立引用表
                await db.execute(text("DELETE FROM idempotency_record WHERE user_id = :u"), {"u": uid})
                # ⑤ 用户行：其余表均以 ondelete=CASCADE 跟随删除
                await db.execute(text("DELETE FROM user WHERE id = :u"), {"u": uid})
                purged += 1
            await db.commit()
            if purged:
                logger.info("purge deactivated users: %d", purged)
        except Exception as e:  # noqa: BLE001
            await db.rollback()
            logger.error("purge deactivated failed: %s", e)


def start_scheduler() -> object | None:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    if not get_settings().scheduler_enabled:
        return None
    scheduler = AsyncIOScheduler(timezone=get_settings().tz)
    scheduler.add_job(job_backup, "cron", hour=2, minute=0, id="backup")
    scheduler.add_job(job_cleanup, "cron", hour=3, minute=0, id="cleanup")
    scheduler.add_job(job_purge_deactivated, "cron", hour=3, minute=30, id="purge")
    scheduler.add_job(job_letter_stat, "cron", hour=4, minute=0, id="letterstat")
    scheduler.start()
    logger.info("scheduler started (02:00 backup / 03:00 cleanup / 03:30 purge / 04:00 letterstat)")
    return scheduler
