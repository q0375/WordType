"""错题本 / 统计 / AI建议 / 设置 / 导出 业务。"""

import csv
import io
import json
import zipfile
from datetime import date, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import AppError
from ..core.idempotency import find_replay, save_snapshot
from ..db.engine import today_str
from ..domain import advice as advice_mod
from ..models import (
    AdviceCache, DailyActivity, DailySetting, ExamRecord, GameRecord, LetterStat,
    TypingRecord, UserWordStat, WrongBookItem, Word,
)
from .stat_ops import get_word, touch_wrongbook


# ---------- 错题本 ----------

async def list_wrongbook(db: AsyncSession, user, resolved: int, source: str | None, pinned: int | None, page: int, page_size: int = 50) -> dict:
    stmt = (
        select(WrongBookItem, Word)
        .join(Word, Word.id == WrongBookItem.word_id)
        .where(WrongBookItem.user_id == user.id, WrongBookItem.resolved == resolved, Word.is_deleted == 0)
    )
    if source:
        stmt = stmt.where(WrongBookItem.source == source)
    if pinned is not None:
        stmt = stmt.where(WrongBookItem.pinned == pinned)
    stmt = stmt.order_by(WrongBookItem.pinned.desc(), WrongBookItem.added_at.desc())
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.offset((page - 1) * page_size).limit(page_size))).all()
    return {
        "items": [
            {
                "word_id": w.id, "spelling": w.spelling, "meaning": w.meaning,
                "source": i.source, "conquer_count": i.conquer_count, "pinned": i.pinned, "added_at": i.added_at,
            }
            for i, w in rows
        ],
        "total": total, "page": page, "page_size": page_size,
    }


async def add_wrongbook(db: AsyncSession, user, word_id: int) -> dict:
    await get_word(db, word_id)
    exists = (
        await db.execute(select(WrongBookItem).where(WrongBookItem.user_id == user.id, WrongBookItem.word_id == word_id))
    ).scalar_one_or_none()
    if exists and exists.resolved == 0:
        raise AppError("WORD_DUPLICATE", "已在错题本中")
    wb = await touch_wrongbook(db, user.id, word_id, "wrong", "practice")
    return {"word_id": word_id, **(wb or {})}


async def patch_wrongbook(db: AsyncSession, user, word_id: int, pinned: int | None, resolved: int | None) -> None:
    row = (
        await db.execute(select(WrongBookItem).where(WrongBookItem.user_id == user.id, WrongBookItem.word_id == word_id))
    ).scalar_one_or_none()
    if row is None:
        raise AppError("NOT_FOUND", "错题不存在")
    if pinned is not None:
        row.pinned = pinned
    if resolved is not None:
        row.resolved = resolved
        row.resolved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if resolved else None


# ---------- 统计 ----------

async def dashboard(db: AsyncSession, user) -> dict:
    today = today_str()
    today_row = (
        await db.execute(select(DailyActivity).where(DailyActivity.user_id == user.id, DailyActivity.date == today))
    ).scalar_one_or_none()

    # 连续天数
    dates = set(
        (
            await db.execute(select(DailyActivity.date).where(DailyActivity.user_id == user.id))
        ).scalars()
    )
    streak = 0
    d = date.today()
    if d.isoformat() not in dates:
        d -= timedelta(days=1)
    while d.isoformat() in dates:
        streak += 1
        d -= timedelta(days=1)

    review_due = (
        await db.execute(
            select(func.count()).select_from(UserWordStat).where(
                UserWordStat.user_id == user.id, UserWordStat.next_review_at.is_not(None), UserWordStat.next_review_at <= today
            )
        )
    ).scalar_one()

    since = (date.today() - timedelta(weeks=12)).isoformat()
    heatmap_rows = (
        await db.execute(
            select(DailyActivity).where(DailyActivity.user_id == user.id, DailyActivity.date >= since).order_by(DailyActivity.date)
        )
    ).scalars().all()

    dist = {"mastered": 0, "consolidating": 0, "danger": 0}
    for p, in (
        await db.execute(select(UserWordStat.proficiency).where(UserWordStat.user_id == user.id))
    ).all():
        if p >= 80:
            dist["mastered"] += 1
        elif p >= 40:
            dist["consolidating"] += 1
        else:
            dist["danger"] += 1

    # WPM 趋势（30 天）
    since30 = (date.today() - timedelta(days=29)).isoformat()
    trend_rows = (
        await db.execute(
            select(
                func.substr(TypingRecord.created_at, 1, 10).label("d"),
                func.avg(TypingRecord.wpm), func.avg(TypingRecord.accuracy),
            )
            .where(TypingRecord.user_id == user.id, TypingRecord.created_at >= since30, TypingRecord.wpm.is_not(None))
            .group_by(text("d")).order_by(text("d"))
        )
    ).all()

    # 近一周 wpm 平均
    week_rows = [r for r in trend_rows if r[0] >= (date.today() - timedelta(days=7)).isoformat()]
    week_wpm = round(sum(r[1] or 0 for r in week_rows) / len(week_rows), 1) if week_rows else 0

    # 未来 7 天复习量
    forecast = []
    for i in range(1, 8):
        target = (date.today() + timedelta(days=i)).isoformat()
        cnt = (
            await db.execute(
                select(func.count()).select_from(UserWordStat).where(UserWordStat.user_id == user.id, UserWordStat.next_review_at == target)
            )
        ).scalar_one()
        forecast.append({"date": target, "count": cnt})

    return {
        "server_date": today,
        "today": {
            "review_due": review_due,
            "new_learned": today_row.new_count if today_row else 0,
            "streak_days": streak,
        },
        "week_wpm": week_wpm,
        "heatmap": [
            {"date": r.date, "new_count": r.new_count, "review_count": r.review_count, "correct": r.correct_count, "wrong": r.wrong_count}
            for r in heatmap_rows
        ],
        "proficiency_dist": dist,
        "wpm_trend": [{"date": r[0], "wpm": round(r[1] or 0, 1), "accuracy": round(r[2] or 0, 3)} for r in trend_rows],
        "forecast_7d": forecast,
    }


async def stats_trend(db: AsyncSession, user, metric: str, source: str | None, days: int) -> list[dict]:
    if metric not in ("wpm", "accuracy"):
        raise AppError("VALIDATION_ERROR", "metric 必须为 wpm/accuracy")
    col = TypingRecord.wpm if metric == "wpm" else TypingRecord.accuracy
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    stmt = (
        select(func.substr(TypingRecord.created_at, 1, 10).label("d"), func.avg(col))
        .where(TypingRecord.user_id == user.id, TypingRecord.created_at >= since, col.is_not(None))
    )
    if source and source != "all":
        stmt = stmt.where(TypingRecord.source == source)
    rows = (await db.execute(stmt.group_by(text("d")).order_by(text("d")))).all()
    return [{"date": r[0], "value": round(r[1] or 0, 3)} for r in rows]


# ---------- AI 建议 ----------

async def get_advice(db: AsyncSession, user) -> dict:
    today = today_str()
    cached = (
        await db.execute(select(AdviceCache).where(AdviceCache.user_id == user.id, AdviceCache.date == today))
    ).scalar_one_or_none()
    if cached is None:
        return await _generate_advice(db, user, today)
    return {
        "date": today,
        "engine": cached.engine,
        "items": json.loads(cached.items_json),
        "refresh_count": cached.refresh_count,
        "refresh_limit": 3,
    }


async def _generate_advice(db: AsyncSession, user, today: str) -> dict:
    # ① 错误率 >60%
    rows = (
        await db.execute(
            select(UserWordStat, Word)
            .join(Word, Word.id == UserWordStat.word_id)
            .where(
                UserWordStat.user_id == user.id,
                (UserWordStat.correct_count + UserWordStat.wrong_count) >= 5,
                UserWordStat.wrong_count * 1.0 / (UserWordStat.correct_count + UserWordStat.wrong_count) > 0.6,
            )
            .limit(50)
        )
    ).all()
    high_error = [
        {"word_id": s.word_id, "spelling": w.spelling, "wrong_count": s.wrong_count, "total_count": s.correct_count + s.wrong_count}
        for s, w in rows
    ]

    # ② 高危且明日到期
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    danger_due = (
        await db.execute(
            select(func.count()).select_from(UserWordStat).where(
                UserWordStat.user_id == user.id, UserWordStat.proficiency < 40, UserWordStat.next_review_at == tomorrow
            )
        )
    ).scalar_one()

    # ③ 连续 7 天未学
    since = (date.today() - timedelta(days=6)).isoformat()
    active = (
        await db.execute(
            select(func.count()).select_from(DailyActivity).where(
                DailyActivity.user_id == user.id, DailyActivity.date >= since, DailyActivity.new_count > 0
            )
        )
    ).scalar_one()
    inactive_days = 7 if active == 0 else 0

    # ④ 指法专项（bigram Top5：错误多优先，其次慢）
    bigram_rows = (
        await db.execute(
            select(LetterStat).where(LetterStat.user_id == user.id, func.length(LetterStat.letter) == 2)
            .order_by(LetterStat.error_count.desc(), LetterStat.avg_delay_ms.desc())
            .limit(5)
        )
    ).scalars().all()
    weak = [r.letter for r in bigram_rows if r.error_count > 0 or r.avg_delay_ms > 0]

    items = advice_mod.build_items(high_error, danger_due, inactive_days if inactive_days else None, weak)

    cached = AdviceCache(user_id=user.id, date=today, items_json=json.dumps(items, ensure_ascii=False), engine="rule", generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    await db.merge(cached)
    await db.flush()
    return {"date": today, "engine": "rule", "items": items, "refresh_count": 0, "refresh_limit": 3}


async def refresh_advice(db: AsyncSession, user) -> dict:
    today = today_str()
    cached = (
        await db.execute(select(AdviceCache).where(AdviceCache.user_id == user.id, AdviceCache.date == today))
    ).scalar_one_or_none()
    if cached is not None and cached.refresh_count >= 3:
        raise AppError("ADVICE_REFRESH_LIMIT", "今日手动刷新已达上限（3 次）")
    out = await _generate_advice(db, user, today)
    row = (
        await db.execute(select(AdviceCache).where(AdviceCache.user_id == user.id, AdviceCache.date == today))
    ).scalar_one()
    row.refresh_count = (cached.refresh_count if cached else 0) + 1
    out["refresh_count"] = row.refresh_count
    return out


# ---------- 设置 ----------

async def get_settings_full(db: AsyncSession, user) -> dict:
    s = (
        await db.execute(select(DailySetting).where(DailySetting.user_id == user.id))
    ).scalar_one_or_none()
    if s is None:
        s = DailySetting(user_id=user.id)
        db.add(s)
        await db.flush()
    data = {
        "daily_new_limit": s.daily_new_limit, "daily_review_limit": s.daily_review_limit,
        "loose_match": s.loose_match, "typing_guide_on": s.typing_guide_on, "tts_on": s.tts_on,
        "review_form": s.review_form, "dictation_show_seconds": s.dictation_show_seconds,
        "practice_group_size": s.practice_group_size, "game_difficulty": s.game_difficulty,
        "game_limited_mode": s.game_limited_mode, "game_key_sound": s.game_key_sound, "exam_time_limit": s.exam_time_limit,
        "exam_pass_score": s.exam_pass_score, "exam_loose_match": s.exam_loose_match,
        "review_wrong_reshow": s.review_wrong_reshow,
        "server_date": today_str(), "server_tz": "Asia/Shanghai",
    }
    return data


async def put_settings(db: AsyncSession, user, payload) -> dict:
    s = (
        await db.execute(select(DailySetting).where(DailySetting.user_id == user.id))
    ).scalar_one_or_none()
    if s is None:
        s = DailySetting(user_id=user.id)
        db.add(s)
    for k, v in payload.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(s, k, v)
    await db.flush()
    return await get_settings_full(db, user)


# ---------- 个人数据导出 ----------

async def export_account_zip(db: AsyncSession, user) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        async def _csv(name: str, header: list[str], rows: list[list]):
            s = io.StringIO()
            s.write("\ufeff")
            w = csv.writer(s)
            w.writerow(header)
            w.writerows(rows)
            zf.writestr(name, s.getvalue())

        stats = (
            await db.execute(select(UserWordStat, Word).join(Word, Word.id == UserWordStat.word_id).where(UserWordStat.user_id == user.id))
        ).all()
        await _csv("words.csv", ["word_id", "spelling", "meaning", "proficiency", "correct", "wrong", "near", "next_review_at"],
                   [[s.word_id, w.spelling, w.meaning, s.proficiency, s.correct_count, s.wrong_count, s.near_miss_count, s.next_review_at or ""] for s, w in stats])

        records = (
            await db.execute(select(TypingRecord).where(TypingRecord.user_id == user.id).order_by(TypingRecord.id.desc()).limit(50000))
        ).scalars().all()
        await _csv("records.csv", ["id", "word_id", "source", "result", "wpm", "accuracy", "created_at"],
                   [[r.id, r.word_id, r.source, r.result, r.wpm or "", r.accuracy or "", r.created_at] for r in records])

        daily = (
            await db.execute(select(DailyActivity).where(DailyActivity.user_id == user.id).order_by(DailyActivity.date))
        ).scalars().all()
        await _csv("daily.csv", ["date", "new_count", "review_count", "correct", "wrong"],
                   [[d.date, d.new_count, d.review_count, d.correct_count, d.wrong_count] for d in daily])

        exams = (
            await db.execute(select(ExamRecord).where(ExamRecord.user_id == user.id).order_by(ExamRecord.id.desc()))
        ).scalars().all()
        await _csv("exam.csv", ["id", "score", "duration", "blur_count", "created_at"],
                   [[e.id, e.score, e.duration, e.blur_count, e.created_at] for e in exams])

        games = (
            await db.execute(select(GameRecord).where(GameRecord.user_id == user.id).order_by(GameRecord.id.desc()))
        ).scalars().all()
        await _csv("game.csv", ["id", "score", "max_combo", "correct_count", "difficulty", "mode", "created_at"],
                   [[g.id, g.score, g.max_combo, g.correct_count, g.difficulty, g.mode, g.created_at] for g in games])

        wb = (
            await db.execute(select(WrongBookItem).where(WrongBookItem.user_id == user.id))
        ).scalars().all()
        await _csv("wrongbook.csv", ["word_id", "source", "conquer_count", "pinned", "resolved", "added_at"],
                   [[i.word_id, i.source, i.conquer_count, i.pinned, i.resolved, i.added_at] for i in wb])
    return buf.getvalue()
