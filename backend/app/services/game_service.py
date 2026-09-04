"""游戏业务：服务端抽池（D27 高危 30% 优先）、结算三重校验 + 计分复核（口径19）。"""

import json
import random
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Request

from ..core.errors import AppError
from ..core.idempotency import find_replay, save_snapshot
from ..db.engine import now_str, today_str
from ..domain import judge as judge_mod
from ..domain.game_score import combo_multiplier, recompute_score
from ..models import GameRecord, GameSession, UserWordStat, Word
from .stat_ops import finish_stat_event, get_or_create_stat, touch_daily, touch_wrongbook
from .study_service import _settings


async def start_game(db: AsyncSession, user, payload) -> dict:
    stmt = select(Word).where(Word.is_deleted == 0)
    if payload.chapter_ids:
        stmt = stmt.where(Word.chapter_id.in_(payload.chapter_ids))
    words = list((await db.execute(stmt)).scalars())
    if not words:
        raise AppError("EMPTY_WORD_POOL", "先去学习几个单词")

    pool_size = min(30, len(words))
    # 高危 30% 优先：proficiency<40 的已学词
    danger_ids = list(
        (
            await db.execute(
                select(UserWordStat.word_id).where(
                    UserWordStat.user_id == user.id,
                    UserWordStat.word_id.in_([w.id for w in words]),
                    UserWordStat.proficiency < 40,
                )
            )
        ).scalars()
    )
    random.shuffle(danger_ids)
    n_danger = min(int(pool_size * 0.3), len(danger_ids))
    chosen_ids = danger_ids[:n_danger]
    rest = [w for w in words if w.id not in set(chosen_ids)]
    random.shuffle(rest)
    for w in rest:
        if len(chosen_ids) >= pool_size:
            break
        chosen_ids.append(w.id)

    session_id = uuid.uuid4().hex
    session = GameSession(
        id=session_id, user_id=user.id,
        word_ids_json=json.dumps(chosen_ids),
        difficulty=payload.difficulty, mode=payload.mode,
        status="active", created_at=now_str(),
        expires_at=(datetime.now() + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S"),
    )
    db.add(session)
    await db.flush()

    by_id = {w.id: w for w in words}
    return {
        "session_id": session_id,
        "difficulty": payload.difficulty,
        "mode": payload.mode,
        "expires_at": session.expires_at,
        "words": [{"word_id": wid, "spelling": by_id[wid].spelling} for wid in chosen_ids if wid in by_id],
    }


async def submit_game(db: AsyncSession, user, request: Request, payload) -> dict:
    idem_key = request.headers.get("Idempotency-Key", "")
    if not idem_key:
        raise AppError("VALIDATION_ERROR", "缺少 Idempotency-Key 请求头")
    replay = await find_replay(db, idem_key, "game/submit", user.id, request)
    if replay is not None:
        return replay

    session = (
        await db.execute(select(GameSession).where(GameSession.id == payload.session_id, GameSession.user_id == user.id))
    ).scalar_one_or_none()
    if session is None:
        raise AppError("NOT_FOUND", "对局不存在")
    if session.status == "settled":
        raise AppError("SESSION_ALREADY_SETTLED", "该对局已结算")
    if session.expires_at < now_str():
        session.status = "void"
        raise AppError("SESSION_EXPIRED", "对局已过期")

    pool = set(json.loads(session.word_ids_json))
    s = await _settings(db, user.id)
    loose = bool(s.loose_match)
    words_by_id = {w.id: w for w in (await db.execute(select(Word).where(Word.id.in_(pool)))).scalars().all()}

    # 三重校验（§5.8）：① word∈池 ② typed 匹配 ③ 耗时 ≥ 词长×40ms
    seq: list[dict] = []
    wrong_word_ids: list[int] = []
    correct_count = 0
    for wlog in payload.words:
        wid = wlog.get("word_id")
        if wid not in pool:
            raise AppError("CHEAT_DETECTED", "存在不在词池中的词", [{"word_id": wid, "reason": "not_in_pool"}])
        typed = wlog.get("typed") or ""
        spelling = words_by_id[wid].spelling
        destroyed = bool(wlog.get("destroyed"))
        matched = judge_mod.judge(typed, spelling, loose) in ("correct", "near")
        if destroyed and not matched:
            raise AppError("CHEAT_DETECTED", "输入串与词不匹配", [{"word_id": wid, "reason": "mismatch"}])
        first_ms = int(wlog.get("first_key_ms") or 0)
        last_ms = int(wlog.get("last_key_ms") or 0)
        if destroyed and (last_ms - first_ms) < len(spelling) * 40:
            raise AppError("CHEAT_DETECTED", "输入耗时异常", [{"word_id": wid, "reason": "too_fast"}])
        seq.append({"destroyed": destroyed, "word_len": len(spelling)})
        if destroyed:
            correct_count += 1
        else:
            wrong_word_ids.append(wid)

    server_score = recompute_score(seq)
    # 偏差 >5%（高度加成客户端计算）以服务端为准
    final_score = payload.score if abs(payload.score - server_score) <= max(server_score, 1) * 0.05 else server_score

    # max_combo 复核
    combo, max_combo = 0, 0
    for item in seq:
        if item["destroyed"]:
            combo += 1
            max_combo = max(max_combo, combo)
        else:
            combo = 0
    max_combo = min(max_combo, payload.max_combo) if payload.max_combo else max_combo

    # 批量：stat / wrongbook / daily / record / session 状态
    wid_list = [w.get("word_id") for w in payload.words]
    for wid, item in zip(wid_list, seq):
        stat, _ = await get_or_create_stat(db, user.id, wid)
        await finish_stat_event(db, user.id, stat, "correct" if item["destroyed"] else "wrong", "game", today_str(), full_sm2=False)
        await touch_wrongbook(db, user.id, wid, "correct" if item["destroyed"] else "wrong", "game")
    await touch_daily(db, user.id, today_str(), correct=correct_count, wrong=len(wrong_word_ids))

    # WPM：正确字符/5/有效分钟
    duration_min = max(payload.duration_ms / 60000, 1 / 60)
    correct_chars = sum(item["word_len"] for item in seq if item["destroyed"])
    wpm = round(correct_chars / 5 / duration_min, 1)

    max_score = (
        await db.execute(
            select(func.max(GameRecord.score)).where(GameRecord.user_id == user.id, GameRecord.is_valid == 1)
        )
    ).scalar_one() or 0
    is_new_record = final_score > max_score

    record = GameRecord(
        user_id=user.id, session_id=session.id, score=final_score, max_combo=max_combo,
        correct_count=correct_count, wpm=wpm, difficulty=payload.difficulty, mode=payload.mode,
        is_valid=1, idempotency_key=idem_key,
    )
    db.add(record)
    session.status = "settled"
    await db.flush()

    out: dict = {
        "valid": True,
        "score": final_score,
        "is_new_record": is_new_record,
        "max_score": max(max_score, final_score),
    }
    from ..core.config import get_settings

    if get_settings().public_deploy:
        total = (
            await db.execute(
                select(func.count()).select_from(
                    select(GameRecord).where(
                        GameRecord.difficulty == payload.difficulty, GameRecord.mode == payload.mode, GameRecord.is_valid == 1,
                        GameRecord.score > final_score,
                    ).subquery()
                )
            )
        ).scalar_one()
        out["rank"] = {"position": total + 1, "total": total + 1}
    await save_snapshot(db, idem_key, "game/submit", user.id, 200, out)
    return out
