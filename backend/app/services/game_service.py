"""游戏业务：服务端抽池（所选范围全部词整池洗牌下发，2026-09-23 改版）、结算三重校验 + 计分复核（口径19）。"""

import json
import random
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Request

from ..core.errors import AppError
from ..core.idempotency import find_replay, save_snapshot
from ..db.engine import now_str, today_str
from ..domain.game_score import fall_seconds, partial_ratio, recompute_score, timed_hp_bonus
from ..models import Chapter, GameRecord, GameSession, TypingRecord, Word, WordBook
from .stat_ops import finish_stat_event, get_or_create_stat, touch_daily, touch_wrongbook, word_wpm


async def _chapter_key(db: AsyncSession, book_id: int | None, chapter_ids: list[int] | None) -> str | None:
    """排行榜分组键（2026-09-23 新口径）：所选章节集合升序去重逗号串（如 "501,502"）。

    空集合，或所选集合恰好等于该书全部有词章节（=前端「不选/全选」，词池与整本一致）→ None（整本榜）。
    分组原则：词池范围相同的人才能互比，多选 2 章与选全本不再同榜。
    """
    ids = sorted({int(x) for x in (chapter_ids or [])})
    if not ids:
        return None
    if book_id is not None:
        all_ids = {
            x for x in (
                await db.execute(
                    select(Word.chapter_id)
                    .join(Chapter, Chapter.id == Word.chapter_id)
                    .where(Word.is_deleted == 0, Chapter.book_id == book_id)
                )
            ).scalars()
            if x is not None
        }
        if all_ids and ids == sorted(all_ids):
            return None
    return ",".join(str(x) for x in ids)


async def start_game(db: AsyncSession, user, payload) -> dict:
    stmt = select(Word).where(Word.is_deleted == 0)
    if payload.chapter_ids:
        stmt = stmt.where(Word.chapter_id.in_(payload.chapter_ids))
    words = list((await db.execute(stmt)).scalars())
    if not words:
        raise AppError("EMPTY_WORD_POOL", "先去学习几个单词")

    # 词池 = 所选范围全部词（2026-09-23 改版，替代旧「30 词池 + 高危 30% 优先 + 跨局去重」）：
    # 开局把范围内全部词洗牌整体下发，前端逐问从池中随机抽、局内不放回——
    # 范围词量大（如 CET4 核心千余词）时整局无重复；全部词用过一轮后前端才进入第二轮兜底。
    chosen_ids = [w.id for w in words]
    random.shuffle(chosen_ids)

    session_id = uuid.uuid4().hex
    # 排行榜分组键在开局固化（服务端权威，submit 不可伪造）：单选=单章键，多选=组合键，全选/不选=整本
    chapter_key = await _chapter_key(db, payload.book_id, payload.chapter_ids)
    session = GameSession(
        id=session_id, user_id=user.id,
        word_ids_json=json.dumps(chosen_ids),
        difficulty=payload.difficulty, mode=payload.mode,
        book_id=payload.book_id, chapter_id=payload.chapter_id, chapter_key=chapter_key,
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
        "words": [
            {"word_id": wid, "spelling": by_id[wid].spelling}
            for wid in chosen_ids
            if wid in by_id
        ],
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
    difficulty = session.difficulty or payload.difficulty
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
        # 新口径：输入完成（长度=词长）即销毁结算，得分比 = 词序相似度（2026-09-20 起 difflib）；未全对销毁前端同样掉血
        if destroyed and len(typed) != len(spelling):
            raise AppError("CHEAT_DETECTED", "输入长度与词长不符", [{"word_id": wid, "reason": "length_mismatch"}])
        first_ms = int(wlog.get("first_key_ms") or 0)
        last_ms = int(wlog.get("last_key_ms") or 0)
        if destroyed and (last_ms - first_ms) < len(spelling) * 40:
            raise AppError("CHEAT_DETECTED", "输入耗时异常", [{"word_id": wid, "reason": "too_fast"}])
        # 得分比 = 字符正确率（销毁与落地/放弃同一口径）；时限 = 该词落地秒数
        limit_s = fall_seconds(spelling, difficulty)
        ratio = partial_ratio(typed, spelling)
        seq.append({
            "destroyed": destroyed, "landed": bool(wlog.get("landed")),
            "word_len": len(spelling), "ratio": ratio, "limit_s": limit_s,
        })
        if destroyed:
            correct_count += 1
        else:
            wrong_word_ids.append(wid)

    server_score = recompute_score(seq)
    # 偏差 >5% 以服务端为准（口径可由服务端完整重算，正常应完全一致）
    base_score = payload.score if abs(payload.score - server_score) <= max(server_score, 1) * 0.05 else server_score
    # 限时模式到时结算：剩余生命 1:1 计入总得分（服务端按词日志重算，2026-09-21 新增）
    hp_bonus = timed_hp_bonus(seq, payload.mode, payload.duration_ms)
    final_score = base_score + hp_bonus

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
        # 主动放弃（Delete / Shift+Backspace：destroyed=false 且 landed=false）只是弃权，不进错题本；
        # 触线落地（landed=true）仍算「没打对」照旧计入（2026-10-03）
        if item["destroyed"] or item["landed"]:
            await touch_wrongbook(db, user.id, wid, "correct" if item["destroyed"] else "wrong", "game")
    await touch_daily(db, user.id, today_str(), correct=correct_count, wrong=len(wrong_word_ids))

    # 每词打字记录（2026-09-23）：仅销毁的词有真实输入耗时；落地/未打完不计。
    # 无 request_id（游戏按局幂等），uq_tr_request 索引对 NULL 不去重，不影响。
    for wlog, item in zip(payload.words, seq):
        if not item["destroyed"]:
            continue
        wid = wlog.get("word_id")
        db.add(
            TypingRecord(
                user_id=user.id, word_id=wid, source="game", result="correct",
                wpm=word_wpm(words_by_id[wid].spelling, int(wlog.get("last_key_ms") or 0) - int(wlog.get("first_key_ms") or 0)),
                accuracy=round(item["ratio"], 4) if item["ratio"] < 1 else 1.0,
            )
        )

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
        correct_count=correct_count, wpm=wpm, difficulty=difficulty, mode=payload.mode,
        book_id=session.book_id, chapter_id=session.chapter_id,
        # 分组键从开局快照拷贝；旧 session（迁移前开局未结算）回落到单章键
        chapter_key=session.chapter_key or (str(session.chapter_id) if session.chapter_id else None),
        is_valid=1, idempotency_key=idem_key,
    )
    db.add(record)
    session.status = "settled"
    await db.flush()

    out: dict = {
        "valid": True,
        "score": final_score,
        "hp_bonus": hp_bonus,
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


async def leaderboard(db: AsyncSession, user, book_id: int, chapter_ids: list[int] | None, mode: str, difficulty: str) -> dict:
    """公共词库排行榜：按 词库 + 章节集合键(NULL=整本) + 模式 + 难度 分组，每人取其最高分参与排名。

    chapter_ids 语义（2026-09-23 新口径）：空=整本榜；单元素=该章榜；多元素=该组合专属榜
    （选 1+2+3 就和同样选 1+2+3 的人比，不与整本榜互混）。所选集合等于该书全部有词章节时
    归一为整本榜（词池与不选完全一致）。
    返回前 10 名（top）与「自己及前后 2 名」（around，最多 5 行）。非公共词库不开放排行榜。
    """
    book = (
        await db.execute(select(WordBook).where(WordBook.id == book_id, WordBook.is_deleted == 0))
    ).scalar_one_or_none()
    if book is None:
        raise AppError("NOT_FOUND", "词库不存在")
    chapter_key = await _chapter_key(db, book_id, chapter_ids)
    base = {
        "is_public": bool(book.is_public),
        "book_id": book_id,
        "book_name": book.name,
        "chapter_key": chapter_key,
        "mode": mode,
        "difficulty": difficulty,
    }
    if not book.is_public:
        # 私有词库不展示他人成绩（前端会给出提示）——只回报自己的最高分
        my_best = (
            await db.execute(
                select(func.max(GameRecord.score)).where(
                    GameRecord.user_id == user.id, GameRecord.book_id == book_id,
                    GameRecord.mode == mode, GameRecord.difficulty == difficulty, GameRecord.is_valid == 1,
                    GameRecord.chapter_key.is_(None) if chapter_key is None else GameRecord.chapter_key == chapter_key,
                )
            )
        ).scalar_one()
        return {**base, "total_players": 1 if my_best else 0, "top": [], "around": [], "me": None, "my_best": my_best}

    cond = "r.book_id = :book AND r.mode = :mode AND r.difficulty = :diff AND r.is_valid = 1"
    params: dict = {"book": book_id, "mode": mode, "diff": difficulty}
    if chapter_key is None:
        cond += " AND r.chapter_key IS NULL"
    else:
        cond += " AND r.chapter_key = :ckey"
        params["ckey"] = chapter_key

    rows = (
        await db.execute(
            text(
                f"""
                SELECT u.id AS user_id, u.username AS username, MAX(r.score) AS best
                FROM game_record r JOIN user u ON u.id = r.user_id
                WHERE {cond}
                GROUP BY u.id, u.username
                ORDER BY best DESC, u.id ASC
                """
            ),
            params,
        )
    ).all()
    board = [
        {"rank": i + 1, "user_id": r.user_id, "username": r.username, "score": r.best, "is_me": r.user_id == user.id}
        for i, r in enumerate(rows)
    ]
    me = next((x for x in board if x["is_me"]), None)
    idx = board.index(me) if me else -1
    around = board[max(0, idx - 2): idx + 3] if idx >= 0 else []
    return {**base, "total_players": len(board), "top": board[:10], "around": around, "me": me, "my_best": me["score"] if me else None}
