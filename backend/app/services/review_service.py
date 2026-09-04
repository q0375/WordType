"""复习模式业务：队列物化（DBD §4.7① 幂等可重入）、作答（SM-2 全量 + D16 映射 + D11 重现）。"""

import json

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import AppError
from ..core.idempotency import find_replay, save_snapshot
from ..db.engine import today_str
from ..domain import judge as judge_mod
from ..models import DailyActivity, DailySetting, ReviewQueue, TypingRecord, Word
from .stat_ops import finish_stat_event, get_or_create_stat, get_word, touch_daily, touch_wrongbook


async def _settings(db: AsyncSession, user_id: int) -> DailySetting:
    s = (await db.execute(select(DailySetting).where(DailySetting.user_id == user_id))).scalar_one_or_none()
    if s is None:
        s = DailySetting(user_id=user_id)
        db.add(s)
        await db.flush()
    return s


async def review_today(db: AsyncSession, user) -> dict:
    s = await _settings(db, user.id)
    today = today_str()

    reviewed_today = (
        await db.execute(
            select(DailyActivity.review_count).where(DailyActivity.user_id == user.id, DailyActivity.date == today)
        )
    ).scalar_one_or_none() or 0
    pending_count = (
        await db.execute(
            select(ReviewQueue).where(
                ReviewQueue.user_id == user.id, ReviewQueue.date == today, ReviewQueue.status == "pending"
            )
        )
    ).scalars().all()
    remaining = max(0, s.daily_review_limit - reviewed_today - len(pending_count))

    # 队列物化（幂等：NOT EXISTS 防重复；uq_rq 兜底）
    if remaining > 0:
        await db.execute(
            text(
                """
                INSERT INTO review_queue (user_id, date, word_id, tier, overdue_days, is_reshow, status, created_at)
                SELECT :uid, :today, s.word_id,
                       CASE WHEN s.next_review_at < :today THEN 0
                            WHEN s.proficiency < 40 THEN 1 ELSE 2 END,
                       MAX(0, CAST(julianday(:today) - julianday(s.next_review_at) AS INTEGER)),
                       0, 'pending', datetime('now','localtime')
                FROM user_word_stat s
                WHERE s.user_id = :uid AND s.next_review_at IS NOT NULL
                  AND s.next_review_at <= :today
                  AND NOT EXISTS (SELECT 1 FROM review_queue q
                                  WHERE q.user_id = s.user_id AND q.date = :today
                                    AND q.word_id = s.word_id AND q.is_reshow = 0)
                ORDER BY (s.next_review_at < :today) DESC,
                         CASE WHEN s.next_review_at < :today
                              THEN -julianday(:today) + julianday(s.next_review_at)
                              ELSE 0 END DESC,
                         s.proficiency ASC, s.word_id ASC
                LIMIT :remaining
                """
            ),
            {"uid": user.id, "today": today, "remaining": remaining},
        )

    rows = (
        await db.execute(
            select(ReviewQueue)
            .where(ReviewQueue.user_id == user.id, ReviewQueue.date == today, ReviewQueue.status == "pending")
            .order_by(ReviewQueue.tier, ReviewQueue.overdue_days.desc(), ReviewQueue.id)
            .limit(remaining)
        )
    ).scalars().all()

    overdue = sum(1 for r in rows if r.tier == 0)
    danger = sum(1 for r in rows if r.tier == 1)
    consolidating = sum(1 for r in rows if r.tier == 2)

    words = {
        w.id: w
        for w in (
            await db.execute(
                select(Word).where(Word.id.in_([r.word_id for r in rows] or [0]), Word.is_deleted == 0)
            )
        ).scalars()
    }
    return {
        "server_date": today,
        "quota": {"limit": s.daily_review_limit, "used": reviewed_today, "remaining": remaining},
        "summary": {"overdue": overdue, "danger": danger, "consolidating": consolidating},
        "queue": [
            {
                "word_id": r.word_id,
                "tier": r.tier,
                "overdue_days": r.overdue_days,
                "reshow": bool(r.is_reshow),
                "spelling": words[r.word_id].spelling if r.word_id in words else None,
                "meaning": words[r.word_id].meaning if r.word_id in words else None,
                "phonetic": words[r.word_id].phonetic if r.word_id in words else None,
            }
            for r in rows
        ],
    }


async def review_answer(db: AsyncSession, user, payload, request=None) -> dict:
    replay = await find_replay(db, payload.request_id, "review/answer", user.id, request)
    if replay is not None:
        return replay
    s = await _settings(db, user.id)
    today = today_str()
    w = await get_word(db, payload.word_id)

    # 额度校验：今日 review_count + pending < limit（重现题受控于队列唯一索引）
    reviewed_today = (
        await db.execute(
            select(DailyActivity.review_count).where(DailyActivity.user_id == user.id, DailyActivity.date == today)
        )
    ).scalar_one_or_none() or 0
    pending_count = len(
        (
            await db.execute(
                select(ReviewQueue).where(
                    ReviewQueue.user_id == user.id, ReviewQueue.date == today, ReviewQueue.status == "pending"
                )
            )
        ).scalars().all()
    )
    if not payload.reshow and reviewed_today + pending_count >= s.daily_review_limit:
        raise AppError("QUOTA_EXCEEDED", "今日复习额度已用完")

    # 找到当日 pending 队列行（重现行为 is_reshow=1）
    q = (
        await db.execute(
            select(ReviewQueue)
            .where(
                ReviewQueue.user_id == user.id,
                ReviewQueue.date == today,
                ReviewQueue.word_id == payload.word_id,
                ReviewQueue.status == "pending",
                ReviewQueue.is_reshow == (1 if payload.reshow else 0),
            )
            .order_by(ReviewQueue.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if q is None:
        raise AppError("QUEUE_ITEM_NOT_PENDING", "该复习题不在待答队列")

    # 判定（self → D16 映射）
    if payload.form == "self":
        if payload.rating not in ("know", "vague", "unknown"):
            raise AppError("VALIDATION_ERROR", "rating 必须为 know/vague/unknown")
        result = {"know": "correct", "vague": "near", "unknown": "wrong"}[payload.rating]
        typed = None
    elif payload.form == "choice":
        # 复习选择题：客户端提交所选释义，服务端按释义比对
        result = "correct" if payload.choice_key == w.meaning else "wrong"
        typed = w.spelling if result == "correct" else ""
    else:  # typing
        typed = payload.typed or ""
        result = judge_mod.judge(typed, w.spelling, loose=bool(s.loose_match))

    stat, _ = await get_or_create_stat(db, user.id, payload.word_id)
    sm2_out = await finish_stat_event(db, user.id, stat, result, "review", today, full_sm2=True)

    q.status = "done"

    if payload.form != "self":
        db.add(
            TypingRecord(
                user_id=user.id, word_id=payload.word_id, source="review", result=result,
                request_id=payload.request_id,
                detail_json=json.dumps(payload.detail, ensure_ascii=False) if payload.detail else None,
            )
        )
    wb = await touch_wrongbook(db, user.id, payload.word_id, result, "review")

    # D11：答错且开关开 → 当日重现一次（uq_rq 唯一索引保证仅一次）
    reshow_scheduled = False
    if result == "wrong" and s.review_wrong_reshow and not payload.reshow:
        exists = (
            await db.execute(
                select(ReviewQueue.id).where(
                    ReviewQueue.user_id == user.id,
                    ReviewQueue.date == today,
                    ReviewQueue.word_id == payload.word_id,
                    ReviewQueue.is_reshow == 1,
                )
            )
        ).scalar_one_or_none()
        if exists is None:
            db.add(ReviewQueue(user_id=user.id, date=today, word_id=payload.word_id, tier=q.tier, overdue_days=q.overdue_days, is_reshow=1))
            reshow_scheduled = True

    await touch_daily(db, user.id, today, review=1, correct=1 if result == "correct" else 0, wrong=1 if result in ("near", "wrong") else 0)

    out = {
        "result": result,
        "sm2": {
            "interval_days": stat.interval_days,
            "ease_factor": round(stat.ease_factor, 3),
            "next_review_at": stat.next_review_at,
        },
        "proficiency": sm2_out["proficiency"],
        "streak_correct": sm2_out["streak_correct"],
        "reshow_scheduled": reshow_scheduled,
    }
    await save_snapshot(db, payload.request_id, "review/answer", user.id, 200, out)
    return out
