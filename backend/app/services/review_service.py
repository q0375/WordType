"""复习模式业务：队列物化（DBD §4.7① 幂等可重入）、作答（SM-2 全量 + D16 映射 + D11 重现）。"""

import json
from datetime import date, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import AppError
from ..core.idempotency import find_replay, save_snapshot
from ..db.engine import today_str
from ..domain import judge as judge_mod
from ..models import DailyActivity, DailySetting, ReviewQueue, TypingRecord, UserWordStat, Word
from .stat_ops import finish_stat_event, get_or_create_stat, get_word, touch_daily, touch_wrongbook, word_wpm


async def _settings(db: AsyncSession, user_id: int) -> DailySetting:
    s = (await db.execute(select(DailySetting).where(DailySetting.user_id == user_id))).scalar_one_or_none()
    if s is None:
        s = DailySetting(user_id=user_id)
        db.add(s)
        await db.flush()
    return s


async def review_today(db: AsyncSession, user) -> dict:
    today = today_str()

    reviewed_today = (
        await db.execute(
            select(DailyActivity.review_count).where(DailyActivity.user_id == user.id, DailyActivity.date == today)
        )
    ).scalar_one_or_none() or 0

    # 队列 = 全部学过的词（2026-09-23 改版）：不再只物化到期词——凡有 stat 的词每日全量进队，
    # 含熟练度≥80 的已掌握毕业词（next_review_at=NULL：tier 判定 NULL<today 为假 → 落 tier2 排队尾）。
    # 无每日上限；排序逾期在前（NULL 视为不逾期），同层熟练度升序；幂等：NOT EXISTS 防重复（uq_rq 兜底）。
    await db.execute(
        text(
            """
            INSERT INTO review_queue (user_id, date, word_id, tier, overdue_days, is_reshow, status, created_at)
            SELECT :uid, :today, s.word_id,
                   CASE WHEN s.next_review_at < :today THEN 0
                        WHEN s.proficiency < 40 THEN 1 ELSE 2 END,
                   MAX(0, CAST(julianday(:today) - julianday(COALESCE(s.next_review_at, :today)) AS INTEGER)),
                   0, 'pending', datetime('now','localtime')
            FROM user_word_stat s
            WHERE s.user_id = :uid
              AND EXISTS (SELECT 1 FROM word w2 WHERE w2.id = s.word_id AND w2.is_deleted = 0)
              AND NOT EXISTS (SELECT 1 FROM review_queue q
                              WHERE q.user_id = s.user_id AND q.date = :today
                                AND q.word_id = s.word_id AND q.is_reshow = 0)
            ORDER BY (s.next_review_at < :today) DESC,
                     CASE WHEN s.next_review_at < :today
                          THEN -julianday(:today) + julianday(s.next_review_at)
                          ELSE 0 END DESC,
                     s.proficiency ASC, s.word_id ASC
            """
        ),
        {"uid": user.id, "today": today},
    )

    rows = (
        await db.execute(
            select(ReviewQueue)
            .where(ReviewQueue.user_id == user.id, ReviewQueue.date == today, ReviewQueue.status == "pending")
            .order_by(ReviewQueue.tier, ReviewQueue.overdue_days.desc(), ReviewQueue.id)
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
        "quota": {"limit": None, "used": reviewed_today, "remaining": None},
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


async def review_plan(db: AsyncSession, user, days: int = 14) -> dict:
    """未来复习计划：SM-2 计算出的 next_review_at 落在 (today, today+days] 的词按天分组。
    2026-09-23 改版后今日队列已含全部学过的词，此处仅作未来排期日历展示，不再排除今日队列。"""
    today = today_str()
    try:
        end = (date.fromisoformat(today) + timedelta(days=max(1, min(days, 90)))).isoformat()
    except ValueError as e:
        raise AppError("VALIDATION_ERROR", "日期计算失败") from e

    rows = (
        await db.execute(
            select(UserWordStat.next_review_at, UserWordStat.word_id, Word.spelling, Word.meaning)
            .join(Word, Word.id == UserWordStat.word_id)
            .where(
                UserWordStat.user_id == user.id,
                UserWordStat.next_review_at.is_not(None),
                UserWordStat.next_review_at > today,
                UserWordStat.next_review_at <= end,
                Word.is_deleted == 0,
            )
            .order_by(UserWordStat.next_review_at, Word.spelling)
        )
    ).all()

    groups: dict[str, list[dict]] = {}
    for next_at, wid, spelling, meaning in rows:
        groups.setdefault(next_at, []).append({"id": wid, "spelling": spelling, "meaning": meaning})
    plan = [{"date": d, "count": len(ws), "words": ws} for d, ws in sorted(groups.items())]
    return {"server_date": today, "days": days, "plan": plan, "total": len(rows)}


async def review_advance(db: AsyncSession, user, target_date: str) -> dict:
    """提前复习（2026-09-23 改版）：队列每日已全量物化，无需再插入；
    返回 target_date 到期且在今日 pending 队列中的词 id 列表，由前端把该批词置顶到当前游标之后。"""
    today = today_str()
    try:
        d = date.fromisoformat(target_date)
    except ValueError as e:
        raise AppError("VALIDATION_ERROR", "日期格式非法") from e
    if d < date.fromisoformat(today):
        raise AppError("VALIDATION_ERROR", "不能选择过去的日期")
    if (d - date.fromisoformat(today)).days > 90:
        raise AppError("VALIDATION_ERROR", "最多提前 90 天")

    # 该日期到期 ∧ 今日队列 pending ∧ 非重现行 → 前端置顶（按熟练度升序）
    ids = (
        await db.execute(
            select(ReviewQueue.word_id)
            .join(
                UserWordStat,
                (UserWordStat.user_id == ReviewQueue.user_id) & (UserWordStat.word_id == ReviewQueue.word_id),
            )
            .where(
                ReviewQueue.user_id == user.id,
                ReviewQueue.date == today,
                ReviewQueue.status == "pending",
                ReviewQueue.is_reshow == 0,
                UserWordStat.next_review_at == target_date,
            )
            .order_by(UserWordStat.proficiency.asc(), ReviewQueue.id)
        )
    ).scalars().all()
    return {"target_date": target_date, "word_ids": list(ids)}


async def review_answer(db: AsyncSession, user, payload, request=None) -> dict:
    replay = await find_replay(db, payload.request_id, "review/answer", user.id, request)
    if replay is not None:
        return replay
    s = await _settings(db, user.id)
    today = today_str()
    w = await get_word(db, payload.word_id)

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
        result = judge_mod.judge(typed, w.spelling, loose=True)  # 宽松判定开关已取消：容差固定 1

    stat, _ = await get_or_create_stat(db, user.id, payload.word_id)
    # 答题前快照：毕业词（p≥80 掌握退出，无排期）答题后不显示 SM-2 反馈（2026-09-23 用户要求）
    was_graduated = stat.next_review_at is None and stat.proficiency >= 80
    sm2_out = await finish_stat_event(db, user.id, stat, result, "review", today, full_sm2=True)

    q.status = "done"

    if payload.form != "self":
        db.add(
            TypingRecord(
                user_id=user.id, word_id=payload.word_id, source="review", result=result,
                # WPM/正确率落库（2026-09-23）：choice 题无打字 → wpm 留空；耗时由前端 first→last key 上报
                wpm=word_wpm(w.spelling, payload.duration_ms) if payload.form == "typing" else None,
                accuracy=1.0 if result == "correct" else round(judge_mod.partial_ratio(typed or "", w.spelling), 4),
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
        "was_graduated": was_graduated,
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
