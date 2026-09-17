"""管理员测试数据调整（dev-data）：为任意用户注入/清理学习数据，方便验证各功能。

仅管理端可用；所有数量参数限 0-200，超出拒绝。
"""

import json
import random
import string
from datetime import date, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import AppError
from ..db.engine import today_str
from ..models import (
    AdviceCache, DailyActivity, ExamRecord, GameRecord, LetterStat, ReviewQueue,
    StudyProgress, TypingRecord, User, UserWordStat, WrongBookItem, Word,
)

LIMIT = 200


async def _target_user(db: AsyncSession, user_id: int | None, fallback: User) -> User:
    if user_id is None:
        return fallback
    u = (await db.execute(select(User).where(User.id == user_id, User.is_deleted == 0))).scalar_one_or_none()
    if u is None:
        raise AppError("NOT_FOUND", "目标用户不存在")
    return u


def _rand_words(db_rows: list, n: int) -> list[int]:
    return random.sample([w.id for w in db_rows], min(n, len(db_rows)))


async def get_summary(db: AsyncSession, user_id: int | None, operator: User) -> dict:
    user = await _target_user(db, user_id, operator)
    uid = user.id
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    async def one(stmt) -> int:
        return (await db.execute(stmt)).scalar_one()

    stat_total = await one(select(func.count()).select_from(UserWordStat).where(UserWordStat.user_id == uid))
    high_error = await one(
        select(func.count()).select_from(UserWordStat).where(
            UserWordStat.user_id == uid,
            (UserWordStat.correct_count + UserWordStat.wrong_count) >= 5,
            UserWordStat.wrong_count * 1.0 / (UserWordStat.correct_count + UserWordStat.wrong_count) > 0.6,
        )
    )
    danger_due = await one(
        select(func.count()).select_from(UserWordStat).where(
            UserWordStat.user_id == uid, UserWordStat.proficiency < 40, UserWordStat.next_review_at == tomorrow
        )
    )
    activity_days = await one(select(func.count()).select_from(DailyActivity).where(DailyActivity.user_id == uid))
    typing_cnt = await one(select(func.count()).select_from(TypingRecord).where(TypingRecord.user_id == uid))
    game_cnt = await one(select(func.count()).select_from(GameRecord).where(GameRecord.user_id == uid))
    wrong_cnt = await one(select(func.count()).select_from(WrongBookItem).where(WrongBookItem.user_id == uid, WrongBookItem.resolved == 0))
    exam_cnt = await one(select(func.count()).select_from(ExamRecord).where(ExamRecord.user_id == uid))
    letter_cnt = await one(select(func.count()).select_from(LetterStat).where(LetterStat.user_id == uid))
    return {
        "user": {"id": user.id, "username": user.username, "role": user.role},
        "word_stat_total": stat_total,
        "high_error": high_error,
        "danger_due_tomorrow": danger_due,
        "activity_days": activity_days,
        "typing_records": typing_cnt,
        "game_records": game_cnt,
        "wrong_book": wrong_cnt,
        "exam_records": exam_cnt,
        "letter_stats": letter_cnt,
    }


async def seed(db: AsyncSession, operator, payload) -> dict:
    user = await _target_user(db, payload.target_user_id, operator)
    uid = user.id
    done: list[str] = []

    # 可用词池（全局未删词）
    words = (
        (await db.execute(select(Word).where(Word.is_deleted == 0).order_by(func.random()).limit(LIMIT))).scalars().all()
    )
    if not words:
        raise AppError("EMPTY_WORD_POOL", "没有可用单词，请先导入词库")

    # ① 高错误率词（错误率>60%，触发「专项练习」建议 + 错题本）
    if payload.high_error_n:
        n = min(payload.high_error_n, LIMIT)
        for wid in _rand_words(words, n):
            total = random.randint(6, 12)
            wrong = total - 1  # 错误率 >60%
            await db.merge(UserWordStat(
                user_id=uid, word_id=wid, proficiency=random.randint(15, 45),
                correct_count=total - wrong, wrong_count=wrong,
                next_review_at=(date.today() + timedelta(days=random.randint(1, 5))).isoformat(),
                last_wrong_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ))
            await db.merge(WrongBookItem(user_id=uid, word_id=wid, source="study", resolved=0))
        done.append(f"高错误率词 ×{n}")

    # ② 高危明日到期（触发「复习」建议 + 复习队列）
    if payload.danger_due_n:
        n = min(payload.danger_due_n, LIMIT)
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        for wid in _rand_words(words, n):
            await db.merge(UserWordStat(
                user_id=uid, word_id=wid, proficiency=random.randint(10, 35),
                correct_count=2, wrong_count=3,
                next_review_at=tomorrow, last_wrong_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ))
            await db.merge(ReviewQueue(user_id=uid, date=tomorrow, word_id=wid, tier=1, status="pending"))
        done.append(f"高危明日到期 ×{n}")

    # ③ 制造连续未学（删除近 N 天活跃记录）
    if payload.inactive_days:
        n = min(payload.inactive_days, 30)
        since = (date.today() - timedelta(days=n - 1)).isoformat()
        await db.execute(delete(DailyActivity).where(DailyActivity.user_id == uid, DailyActivity.date >= since))
        done.append(f"已清除近 {n} 天活跃记录（连续未学）")

    # ④ 弱指法 bigram（触发「指法专项」建议）
    if payload.weak_bigram_n:
        n = min(payload.weak_bigram_n, LIMIT)
        made = set()
        while len(made) < n:
            bg = "".join(random.choices(string.ascii_lowercase, k=2))
            if bg in made:
                continue
            made.add(bg)
            await db.merge(LetterStat(
                user_id=uid, letter=bg, avg_delay_ms=random.randint(260, 480),
                total_count=random.randint(30, 90), error_count=random.randint(6, 20),
            ))
        done.append(f"弱指法组合 ×{n}")

    # ⑤ 打字记录（近 7 天趋势，供统计图表与 LLM 上下文）
    if payload.typing_count:
        n = min(payload.typing_count, LIMIT)
        wpm = payload.typing_wpm if payload.typing_wpm is not None else 40.0
        acc = payload.typing_accuracy if payload.typing_accuracy is not None else 0.92
        for _ in range(n):
            wid = random.choice(words).id
            jitter_w = wpm * random.uniform(0.85, 1.15)
            jitter_a = min(1.0, acc * random.uniform(0.95, 1.05))
            created = (datetime.now() - timedelta(days=random.randint(0, 6), minutes=random.randint(0, 600))).strftime("%Y-%m-%d %H:%M:%S")
            db.add(TypingRecord(
                user_id=uid, word_id=wid, source=random.choice(["study", "review"]),
                result="correct" if random.random() < acc else "wrong",
                wpm=round(jitter_w, 1), accuracy=round(jitter_a, 3),
                detail_json=json.dumps({"seeded": True}), created_at=created,
            ))
        done.append(f"打字记录 ×{n}（约 {wpm} WPM / {acc:.0%} 正确率）")

    # ⑥ 游戏记录
    if payload.game_count:
        n = min(payload.game_count, LIMIT)
        for _ in range(n):
            db.add(GameRecord(
                user_id=uid, score=random.randint(50, 600), max_combo=random.randint(2, 18),
                correct_count=random.randint(5, 40), wpm=round(random.uniform(25, 70), 1),
                difficulty=random.choice(["easy", "normal", "hard"]), mode=random.choice(["endless", "timed"]),
                is_valid=1, created_at=(datetime.now() - timedelta(days=random.randint(0, 6))).strftime("%Y-%m-%d %H:%M:%S"),
            ))
        done.append(f"游戏记录 ×{n}")

    # 造数后当日建议缓存失效，建议按新数据重算
    await db.execute(delete(AdviceCache).where(AdviceCache.user_id == uid, AdviceCache.date == today_str()))

    summary = await get_summary(db, user.id, operator)
    return {"done": done, "summary": summary}


async def clear_all(db: AsyncSession, operator, user_id: int | None) -> dict:
    user = await _target_user(db, user_id, operator)
    uid = user.id
    tables = [UserWordStat, LetterStat, DailyActivity, TypingRecord, GameRecord,
              WrongBookItem, ExamRecord, StudyProgress, ReviewQueue, AdviceCache]
    total = 0
    for t in tables:
        r = await db.execute(delete(t).where(t.user_id == uid))
        total += r.rowcount or 0
    return {"cleared_rows": total, "summary": await get_summary(db, user.id, operator)}
