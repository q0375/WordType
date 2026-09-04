"""共享统计写路径：stat 行获取/更新、sync_lifecycle、错题本联动、DailyActivity upsert（口径21）。"""

import json
from datetime import datetime

from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import AppError
from ..domain import sm2
from ..models import DailyActivity, UserWordStat, WrongBookItem, Word


async def get_or_create_stat(db: AsyncSession, user_id: int, word_id: int) -> tuple[UserWordStat, bool]:
    row = (
        await db.execute(
            select(UserWordStat).where(UserWordStat.user_id == user_id, UserWordStat.word_id == word_id)
        )
    ).scalar_one_or_none()
    if row:
        return row, False
    row = UserWordStat(user_id=user_id, word_id=word_id)
    db.add(row)
    await db.flush()
    return row, True


async def get_word(db: AsyncSession, word_id: int) -> Word:
    w = (await db.execute(select(Word).where(Word.id == word_id, Word.is_deleted == 0))).scalar_one_or_none()
    if not w:
        raise AppError("NOT_FOUND", "单词不存在")
    return w


def stat_snapshot(stat: UserWordStat) -> dict:
    return {
        "proficiency": stat.proficiency,
        "correct_count": stat.correct_count,
        "wrong_count": stat.wrong_count,
        "near_miss_count": stat.near_miss_count,
        "streak_correct": stat.streak_correct,
        "ease_factor": stat.ease_factor,
        "interval_days": stat.interval_days,
        "next_review_at": stat.next_review_at,
    }


def apply_stat_fields(stat: UserWordStat, updates: dict) -> None:
    for k, v in updates.items():
        setattr(stat, k, v)


async def finish_stat_event(
    db: AsyncSession,
    user_id: int,
    stat: UserWordStat,
    result: str,
    source: str,
    today: str,
    full_sm2: bool = False,
) -> dict:
    """按路径更新 stat 并执行 sync_lifecycle；返回 {proficiency, next_review_at, streak_correct}。"""
    updates = sm2.apply_review_sm2(stat_snapshot(stat), result, today) if full_sm2 else sm2.apply_increment(stat_snapshot(stat), result)
    apply_stat_fields(stat, updates)
    last_col = "last_correct_at" if result == "correct" else "last_wrong_at"
    setattr(stat, last_col, now_ts())
    lc = sm2.sync_lifecycle(result, stat.proficiency, stat.next_review_at, stat.interval_days, today)
    apply_stat_fields(stat, lc)
    return {
        "proficiency": stat.proficiency,
        "next_review_at": stat.next_review_at,
        "streak_correct": stat.streak_correct,
    }


async def touch_wrongbook(db: AsyncSession, user_id: int, word_id: int, result: str, source: str) -> dict | None:
    """对→conquer+1（连对3移出）；错→入本/归零（口径15 upsert）；近似不动（N1）。"""
    action = sm2.wrongbook_on_result(result)
    if action == "none":
        return None
    row = (
        await db.execute(
            select(WrongBookItem).where(WrongBookItem.user_id == user_id, WrongBookItem.word_id == word_id)
        )
    ).scalar_one_or_none()
    if action == "add":
        if row is None:
            row = WrongBookItem(user_id=user_id, word_id=word_id, source=source, conquer_count=0, resolved=0)
            db.add(row)
        else:
            row.resolved = 0
            row.resolved_at = None
            row.conquer_count = 0
            row.added_at = now_ts()
            row.source = source
    else:  # conquer
        if row is not None and row.resolved == 0:
            row.conquer_count += 1
            if row.conquer_count >= 3:
                row.resolved = 1
                row.resolved_at = now_ts()
    return {
        "counted": True,
        "conquer_count": row.conquer_count if row is not None else 0,
    }


async def touch_daily(
    db: AsyncSession, user_id: int, today: str, new: int = 0, review: int = 0, correct: int = 0, wrong: int = 0
) -> None:
    """实时 upsert（口径21）；near 计入 wrong（N2）。自评不计 correct/wrong（调用方控制）。"""
    await db.execute(
        text(
            """
            INSERT INTO daily_activity (user_id, date, new_count, review_count, correct_count, wrong_count)
            VALUES (:u, :d, :n, :r, :c, :w)
            ON CONFLICT(user_id, date) DO UPDATE SET
              new_count = new_count + :n,
              review_count = review_count + :r,
              correct_count = correct_count + :c,
              wrong_count = wrong_count + :w
            """
        ),
        {"u": user_id, "d": today, "n": new, "r": review, "c": correct, "w": wrong},
    )


def now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_detail(detail: dict | None) -> dict:
    if not detail or not isinstance(detail, dict):
        return {}
    return detail
