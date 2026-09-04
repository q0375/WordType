"""学习模式业务：next（续学+额度）、self-rate（D8）、dictation（D17）。"""

import json
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import AppError
from ..core.idempotency import find_replay, save_snapshot
from ..db.engine import today_str
from ..domain import judge as judge_mod
from ..domain import sm2
from ..models import DailyActivity, DailySetting, StudyProgress, TypingRecord, UserWordStat, Word
from .stat_ops import apply_stat_fields, get_or_create_stat, get_word, touch_daily, touch_wrongbook


async def _settings(db: AsyncSession, user_id: int) -> DailySetting:
    s = (await db.execute(select(DailySetting).where(DailySetting.user_id == user_id))).scalar_one_or_none()
    if s is None:
        s = DailySetting(user_id=user_id)
        db.add(s)
        await db.flush()
    return s


async def _scope_word_ids(db: AsyncSession, scope: str | None) -> list[int]:
    stmt = select(Word.id).where(Word.is_deleted == 0)
    if scope:
        try:
            kind, raw_id = scope.split(":")
            rid = int(raw_id)
        except ValueError as e:
            raise AppError("VALIDATION_ERROR", "scope 格式非法") from e
        if kind == "chapter":
            stmt = stmt.where(Word.chapter_id == rid)
        elif kind == "book":
            stmt = stmt.where(Word.book_id == rid)
        else:
            raise AppError("VALIDATION_ERROR", "scope 类型非法")
    return list((await db.execute(stmt.order_by(Word.id))).scalars())


async def _new_used(db: AsyncSession, user_id: int) -> int:
    return (
        await db.execute(
            select(DailyActivity.new_count).where(DailyActivity.user_id == user_id, DailyActivity.date == today_str())
        )
    ).scalar_one_or_none() or 0


async def study_next(db: AsyncSession, user, scope: str | None) -> dict:
    s = await _settings(db, user.id)
    today = today_str()
    used = await _new_used(db, user.id)
    word_ids = await _scope_word_ids(db, scope)

    position = None
    continue_available = False
    if scope and scope.startswith("chapter:"):
        chapter_id = int(scope.split(":")[1])
        prog = (
            await db.execute(
                select(StudyProgress).where(StudyProgress.user_id == user.id, StudyProgress.chapter_id == chapter_id)
            )
        ).scalar_one_or_none()
        if prog is not None:
            position = {"chapter_id": chapter_id, "last_word_index": prog.last_word_index}
            continue_available = prog.last_word_index > 0

    next_word = None
    if used < s.daily_new_limit and word_ids:
        stat_ids = set(
            (
                await db.execute(
                    select(UserWordStat.word_id).where(UserWordStat.word_id.in_(word_ids), UserWordStat.user_id == user.id)
                )
            ).scalars()
        )
        first_new = next((wid for wid in word_ids if wid not in stat_ids), None)
        if first_new is not None:
            w = await get_word(db, first_new)
            next_word = {
                "id": w.id, "spelling": w.spelling, "meaning": w.meaning,
                "phonetic": w.phonetic, "example": w.example,
                "chapter_id": w.chapter_id, "index": word_ids.index(first_new),
            }
    return {
        "server_date": today,
        "position": position,
        "continue_available": continue_available,
        "quota": {"used": used, "limit": s.daily_new_limit},
        "next_word": next_word,
    }


def _d17_map(result_or_rating: str) -> str:
    """D8/D17 统一映射：判定三态 ↔ 自评三态。"""
    return {"correct": "know", "near": "vague", "wrong": "unknown", "know": "know", "vague": "vague", "unknown": "unknown"}[result_or_rating]


async def self_rate(db: AsyncSession, user, word_id: int, rating: str, request_id: str, request=None) -> dict:
    replay = await find_replay(db, request_id, "study/self-rate", user.id, request)
    if replay is not None:
        return replay
    await get_word(db, word_id)
    today = today_str()
    stat, created = await get_or_create_stat(db, user.id, word_id)
    if not created:
        pass
    else:
        used = await _new_used(db, user.id)
        if used >= (await _settings(db, user.id)).daily_new_limit:
            raise AppError("QUOTA_EXCEEDED", "今日新词额度已用完")

    updates = sm2.apply_study_set(_d17_map(rating), today)
    apply_stat_fields(stat, updates)
    reshow = rating == "unknown"
    await touch_daily(db, user.id, today, new=1 if created else 0)  # 自评不计 correct/wrong（口径21）
    payload = {
        "result": "rated",
        "proficiency": stat.proficiency,
        "next_review_at": stat.next_review_at,
        "streak_correct": stat.streak_correct,
        "reshow": reshow,
    }
    await save_snapshot(db, request_id, "study/self-rate", user.id, 200, payload)
    return payload


async def dictation(db: AsyncSession, user, word_id: int, typed: str, duration_ms: int, detail: dict | None, request_id: str, request=None) -> dict:
    replay = await find_replay(db, request_id, "study/dictation", user.id, request)
    if replay is not None:
        return replay
    w = await get_word(db, word_id)
    s = await _settings(db, user.id)
    result = judge_mod.judge(typed, w.spelling, loose=bool(s.loose_match))

    today = today_str()
    stat, created = await get_or_create_stat(db, user.id, word_id)
    if created:
        used = await _new_used(db, user.id)
        if used >= s.daily_new_limit:
            raise AppError("QUOTA_EXCEEDED", "今日新词额度已用完")

    updates = sm2.apply_study_set(_d17_map(result), today)
    apply_stat_fields(stat, updates)
    reshow = result == "wrong"

    db.add(
        TypingRecord(
            user_id=user.id, word_id=word_id, source="study", result=result,
            request_id=request_id,
            detail_json=json.dumps(detail, ensure_ascii=False) if detail else None,
        )
    )
    if result == "wrong":
        await touch_wrongbook(db, user.id, word_id, "wrong", "study")
    await touch_daily(
        db, user.id, today, new=1 if created else 0,
        correct=1 if result == "correct" else 0,
        wrong=1 if result in ("near", "wrong") else 0,  # N2
    )
    payload = {"result": result, "proficiency": stat.proficiency, "next_review_at": stat.next_review_at, "reshow": reshow}
    await save_snapshot(db, request_id, "study/dictation", user.id, 200, payload)
    return payload


async def save_study_position(db: AsyncSession, user_id: int, chapter_id: int, last_word_index: int) -> None:
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    stmt = sqlite_insert(StudyProgress).values(
        user_id=user_id, chapter_id=chapter_id, last_word_index=last_word_index, updated_at=today_str()
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "chapter_id"], set_={"last_word_index": last_word_index, "updated_at": today_str()}
    )
    await db.execute(stmt)
