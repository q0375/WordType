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
from ..domain.judge import partial_ratio
from ..db.engine import today_str
from ..domain import judge as judge_mod
from ..models import Chapter, TypingRecord, Word
from .stat_ops import finish_stat_event, get_or_create_stat, get_word, touch_daily, touch_wrongbook, word_wpm

# qid → {word_id, correct_key, word_len, ts}，TTL 2h
_choice_store: dict[str, dict] = {}
_CHOICE_TTL = 7200


def _prune_choice_store() -> None:
    now = time.time()
    for k in [k for k, v in _choice_store.items() if now - v["ts"] > _CHOICE_TTL]:
        _choice_store.pop(k, None)


async def _pool_words(db: AsyncSession, chapter_ids: list[int], word_ids: list[int] | None = None) -> list[Word]:
    stmt = select(Word).where(Word.is_deleted == 0)
    if word_ids:
        # 仪表盘建议直达：按指定词组卷（word_ids 优先于章节，两者的词可能不在所选章节内）
        stmt = stmt.where(Word.id.in_(word_ids))
    else:
        stmt = stmt.where(Word.chapter_id.in_(chapter_ids))
    return list((await db.execute(stmt.order_by(Word.id))).scalars())


def _mask_example(spelling: str, example: str) -> str | None:
    import re

    pattern = re.compile(re.escape(spelling), re.IGNORECASE)
    if not pattern.search(example):
        return None
    return pattern.sub("_" * len(spelling), example), len(spelling)


async def create_session(
    db: AsyncSession,
    user,
    chapter_ids: list[int],
    types: list[str],
    group_size: int,
    word_ids: list[int] | None = None,
) -> dict:
    _prune_choice_store()
    pool = await _pool_words(db, chapter_ids, word_ids)
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
                # 缺音标时回退给单词本身，避免题干空白
                items.append({"word": w, "payload": {"phonetic": w.phonetic or w.spelling, "options": options}, "correct_key": correct_key})
            by_type[t] = items
        elif t == "listening":
            # 客户端 TTS 需要拼写才能合成读音（spec §5.5 payload={} 的落定补充）
            by_type[t] = [{"word": w, "payload": {"spelling": w.spelling}} for w in pool]
        elif t == "cloze":
            items = []
            for w in pool:
                masked = _mask_example(w.spelling, w.example) if w.example else None
                if masked:
                    # 2026-10-04：挖空题补中文释义，前端英文挖空句一行 + 中文释义一行
                    items.append({
                        "word": w,
                        "payload": {
                            "sentence_masked": masked[0],
                            "sentence": w.example,
                            "meaning": w.meaning,
                            "answer_len": masked[1],
                        },
                    })
            by_type[t] = items
        else:
            raise AppError("VALIDATION_ERROR", f"未知题型 {t}")

    questions: list[dict] = []
    type_cycle = [t for t in types if by_type.get(t)]
    # 2026-10-04：勾了但一题都出不来（如挖空无例句）的题型要回报给前端提示，
    # 否则只勾 cloze 时会拿到空题组、前端 running=true 而 current=null → 整页空白。
    skipped = [
        {"type": t, "reason": "no_example" if t == "cloze" else "no_question"}
        for t in types
        if t not in type_cycle
    ]
    if not type_cycle:
        raise AppError(
            "EMPTY_QUESTION_POOL",
            "所选范围内没有可用的例句挖空题，换个章节或取消勾选「例句挖空」" if types == ["cloze"]
            else "所选范围内没有可出的题目，请调整章节或题型",
        )
    # 全池洗一次牌，各题型按同一随机词序排列：题型均衡、每次组卷不同
    word_order = [w.id for w in pool]
    random.shuffle(word_order)
    rank = {wid: i for i, wid in enumerate(word_order)}
    buckets = {t: sorted(by_type[t], key=lambda it: rank[it["word"].id]) for t in type_cycle}
    # 不复用词：每题型内每词最多一题，组规格大于词池时实际题数取决于单词数
    cursors = {t: 0 for t in type_cycle}
    while len(questions) < group_size:
        progressed = False
        for t in type_cycle:
            if len(questions) >= group_size:
                break
            bucket = buckets[t]
            if cursors[t] >= len(bucket):
                continue
            item = bucket[cursors[t]]
            cursors[t] += 1
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
    return {"questions": questions, "skipped": skipped}


async def answer(db: AsyncSession, user, payload, request=None) -> dict:
    replay = await find_replay(db, payload.request_id, "practice/answer", user.id, request)
    if replay is not None:
        return replay
    w = await get_word(db, payload.word_id)

    if payload.type == "choice":
        meta = _choice_store.get(payload.qid)
        if meta is None or meta["word_id"] != payload.word_id:
            raise AppError("VALIDATION_ERROR", "题目已失效，请重新开始练习")
        result = "correct" if payload.choice_key == meta["correct_key"] else "wrong"
        typed = meta["spelling"] if result == "correct" else ""
    else:
        typed = payload.typed or ""
        result = judge_mod.judge(typed, w.spelling, loose=True)  # 宽松判定开关已取消：容差固定 1

    stat, _ = await get_or_create_stat(db, user.id, payload.word_id)
    sm2_out = await finish_stat_event(db, user.id, stat, result, "practice", today_str(), full_sm2=False)

    ratio = partial_ratio(typed, w.spelling)
    db.add(
        TypingRecord(
            user_id=user.id, word_id=payload.word_id, source="practice", result=result,
            # WPM/正确率落库（2026-09-23）：choice 题无打字 → wpm 留空
            wpm=word_wpm(w.spelling, payload.duration_ms) if payload.type != "choice" else None,
            accuracy=1.0 if result == "correct" else round(ratio, 4),
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
        # 连续得分比（difflib）：非完全正确时前端统一展示「得分比 X%」，
        # 取代旧「近似正确 / 答错」两套话术（2026-09-21 口径统一）
        "ratio": round(ratio, 4),
        "proficiency": sm2_out["proficiency"],
        "streak_correct": sm2_out["streak_correct"],
        "wrongbook": wb,
        # 答错/近似时前端展示正确答案（2026-09-19 新增）
        "answer": w.spelling,
        "meaning": w.meaning,
    }
    await save_snapshot(db, payload.request_id, "practice/answer", user.id, 200, out)
    return out
