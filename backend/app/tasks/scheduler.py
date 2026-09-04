"""定时任务（APScheduler 进程内单实例）：02:00 备份 / 03:00 清理 / 04:00 LetterStat 聚合对账。"""

import logging
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import select, text, update

from ..core.config import get_settings
from ..db.engine import get_sessionmaker
from ..models import ExamPaper, GameSession, IdempotencyRecord, ImportJob, LetterStat, ReviewQueue, AdviceCache, TypingRecord

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
            import json

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


def start_scheduler() -> object | None:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    if not get_settings().scheduler_enabled:
        return None
    scheduler = AsyncIOScheduler(timezone=get_settings().tz)
    scheduler.add_job(job_backup, "cron", hour=2, minute=0, id="backup")
    scheduler.add_job(job_cleanup, "cron", hour=3, minute=0, id="cleanup")
    scheduler.add_job(job_letter_stat, "cron", hour=4, minute=0, id="letterstat")
    scheduler.start()
    logger.info("scheduler started (02:00 backup / 03:00 cleanup / 04:00 letterstat)")
    return scheduler
