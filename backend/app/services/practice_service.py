"""练习模式业务：组卷（四题型，无服务端组表 DBD 反向决策）、答题权威判定。
choice 题正确项 key 存进程内 LRU（单进程架构 ADR-1，重启失效仅影响未完成组）。"""

import json
import random
import time
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import AppError
from ..core.idempotency import find_replay, save_snapshot
from ..db.engine import today_str
from ..domain import judge as judge_mod
from ..models import Chapter, TypingRecord, Word
from .stat_ops import finish_stat_event, get_or_create_stat, get_word, touch_daily, touch_wrongbook
from .study_service import _settings

# qid → {word_id, correct_key, word_len, ts}，TTL 2h
_choice_store: dict[str, dict] = {}
_CHOICE_TTL = 7200


def _prune_choice_store() -> None:
    now = time.time()
    for k in [k for k, v in _choice_store.items() if now - v["ts"] > _CHOICE_TTL]:
        _choice_store.pop(k, None)


async def _pool_words(db: AsyncSession, chapter_ids: list[int]) -> list[Word]:
    return list(
        (
            await db.execute(
                select(Word)
                .where(Word.chapter_id.in_(chapter_ids), Word.is_deleted == 0)
                .order_by(Word.id)
            )
        ).scalars()
    )


def _mask_example(spelling: str, example: str) -> str | None:
    import re

    pattern = re.compile(re.escape(spelling), re.IGNORECASE)
    if not pattern.search(example):
        return None
    return pattern.sub("_" * len(spelling), example), len(spelling)


async def create_session(db: AsyncSession, user, chapter_ids: list[int], types: list[str], group_size: int) -> dict:
    _prune_choice_store()
    pool = await _pool_words(db, chapter_ids)
    if not pool:
        raise AppError("EMPTY_WORD_POOL", "所选范围内没有单词")

    all_meanings = [w.meaning for w in pool]
    by_type: dict[str, list[dict]] = {t: [] for t in types}
    for t in types:
        if t == "dictation":
            by_type[t] = [{"word": w, "payload": {"meaning": w.meaning}} for w in pool]
        elif t == "choice":
            items = []
            for w in pool:
                distractors = [m for m in all_meanings if m != w.meaning]
                if len(distractors) < 3:
                    continue
                ds = random.sample(distractors, 3)
                options = [{"key": k, "text": txt} for k, txt in zip("ABCD", random.sample(ds + [w.meaning], 4))]
                correct_key = next(o["key"] for o in options if o["text"] == w.meaning)
                items.append({"word": w, "payload": {"phonetic": w.phonetic, "options": options}, "correct_key": correct_key})
            by_type[t] = items
        elif t == "listening":
            # 客户端 TTS 需要拼写才能合成读音（spec §5.5 payload={} 的落定补充）
            by_type[t] = [{"word": w, "payload": {"spelling": w.spelling}} for w in pool]
        elif t == "cloze":
            items = []
            for w in pool:
                masked = _mask_example(w.spelling, w.example) if w.example else None
                if masked:
                    items.append({"word": w, "payload": {"sentence_masked": masked[0], "answer_len": masked[1]}})
            by_type[t] = items
        else:
            raise AppError("VALIDATION_ERROR", f"未知题型 {t}")

    questions: list[dict] = []
    idx = 0
    type_cycle = [t for t in types if by_type.get(t)]
    # 均匀轮询；池空题型跳过并按比例补偿
    while len(questions) < group_size:
        progressed = False
        for t in type_cycle:
            if len(questions) >= group_size:
                break
            bucket = by_type[t]
            if not bucket:
                continue
            item = bucket[idx % len(bucket)]
            qid = uuid.uuid4().hex
            q = {"qid": qid, "word_id": item["word"].id, "type": t, "payload": item["payload"]}
            if t == "choice":
                _choice_store[qid] = {
                    "word_id": item["word"].id,
                    "correct_key": item["correct_key"],
                    "spelling": item["word"].spelling,
                    "ts": time.time(),
                }
            questions.append(q)
            progressed = True
        if not progressed:
            break
        idx += 1
    return {"questions": questions}


async def answer(db: AsyncSession, user, payload, request=None) -> dict:
    replay = await find_replay(db, payload.request_id, "practice/answer", user.id, request)
    if replay is not None:
        return replay
    w = await get_word(db, payload.word_id)
    s = await _settings(db, user.id)
    loose = bool(s.loose_match)

    if payload.type == "choice":
        meta = _choice_store.get(payload.qid)
        if meta is None or meta["word_id"] != payload.word_id:
            raise AppError("VALIDATION_ERROR", "题目已失效，请重新开始练习")
        result = "correct" if payload.choice_key == meta["correct_key"] else "wrong"
        typed = meta["spelling"] if result == "correct" else ""
    else:
        typed = payload.typed or ""
        result = judge_mod.judge(typed, w.spelling, loose)

    stat, _ = await get_or_create_stat(db, user.id, payload.word_id)
    sm2_out = await finish_stat_event(db, user.id, stat, result, "practice", today_str(), full_sm2=False)

    db.add(
        TypingRecord(
            user_id=user.id, word_id=payload.word_id, source="practice", result=result,
            request_id=payload.request_id,
            detail_json=json.dumps(payload.detail, ensure_ascii=False) if payload.detail else None,
        )
    )
    wb = await touch_wrongbook(db, user.id, payload.word_id, result, "practice")
    correct = 1 if result == "correct" else 0
    wrong = 1 if result in ("near", "wrong") else 0  # N2
    await touch_daily(db, user.id, today_str(), correct=correct, wrong=wrong)

    out = {
        "result": result,
        "proficiency": sm2_out["proficiency"],
        "streak_correct": sm2_out["streak_correct"],
        "wrongbook": wb,
    }
    await save_snapshot(db, payload.request_id, "practice/answer", user.id, 200, out)
    return out
