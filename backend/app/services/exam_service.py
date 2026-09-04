"""考核业务：组卷（D22 已学优先）、失焦上报、交卷计分（D20+头幂等）、作废、历史。"""

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
from ..domain.exam_scoring import question_raw, score_exam
from ..models import ExamPaper, ExamRecord, UserWordStat, Word
from .stat_ops import finish_stat_event, get_or_create_stat, touch_daily, touch_wrongbook
from .study_service import _settings


def _payload_for(w: Word, qtype: str, all_meanings: list[str] | None = None) -> dict:
    if qtype == "dictation":
        return {"meaning": w.meaning}
    if qtype == "listening":
        # 客户端发音需要拼写（与练习口径一致）
        return {"spelling": w.spelling}
    if qtype == "cloze":
        import re

        masked = re.sub(re.escape(w.spelling), "_" * len(w.spelling), w.example, flags=re.IGNORECASE) if w.example else None
        if not masked or masked == w.example:
            masked = f"_{w.spelling[0]}{'_' * (len(w.spelling) - 1)}"  # 回退：首字母提示
        return {"sentence_masked": masked, "answer_len": len(w.spelling)}
    if qtype == "choice":
        distractors = [m for m in (all_meanings or []) if m != w.meaning]
        ds = random.sample(distractors, min(3, len(distractors)))
        options = [{"key": k, "text": t} for k, t in zip("ABCD", random.sample(ds + [w.meaning], len(ds) + 1))]
        return {"phonetic": w.phonetic, "options": options}
    return {}


async def start_exam(db: AsyncSession, user, payload) -> dict:
    active = (
        await db.execute(
            select(ExamPaper).where(ExamPaper.user_id == user.id, ExamPaper.status == "active")
        )
    ).scalars().first()
    if active is not None:
        raise AppError("EXAM_ALREADY_ACTIVE", "已有进行中的考核卷")

    s = await _settings(db, user.id)
    chapter_id = payload.chapter_id
    pool = list(
        (
            await db.execute(select(Word).where(Word.chapter_id == chapter_id, Word.is_deleted == 0).order_by(Word.id))
        ).scalars()
    )
    if not pool:
        raise AppError("EMPTY_WORD_POOL", "该章节没有单词")
    pool_ids = {w.id for w in pool}
    total = sum(payload.type_counts.values())
    if total <= 0:
        raise AppError("VALIDATION_ERROR", "题量必须大于 0")

    # D22 已学优先
    learned_ids = list(
        (
            await db.execute(
                select(UserWordStat.word_id).where(
                    UserWordStat.user_id == user.id, UserWordStat.word_id.in_(pool_ids)
                )
            )
        ).scalars()
    )
    random.shuffle(learned_ids)
    rest = [w.id for w in pool if w.id not in set(learned_ids)]
    random.shuffle(rest)
    ordered = learned_ids + rest
    if len(ordered) < total:
        raise AppError("EMPTY_WORD_POOL", "章节词量不足以组卷")

    words_by_id = {w.id: w for w in pool}
    all_meanings = [w.meaning for w in pool]
    questions, answer_key = [], []
    idx = 0
    for qtype, count in payload.type_counts.items():
        for _ in range(count):
            if idx >= len(ordered):
                break
            wid = ordered[idx]
            idx += 1
            w = words_by_id[wid]
            qid = uuid.uuid4().hex
            questions.append({"qid": qid, "type": qtype, "word_id": wid, "payload": _payload_for(w, qtype, all_meanings)})
            answer_key.append({"qid": qid, "word_id": wid, "type": qtype, "spelling": w.spelling, "meaning": w.meaning})

    paper_id = uuid.uuid4().hex
    started = datetime.now()
    expires = started + timedelta(minutes=payload.time_limit_min)
    config = {
        "chapter_id": chapter_id,
        "type_counts": payload.type_counts,
        "time_limit_min": payload.time_limit_min,
        "pass_score": payload.pass_score,
        "loose_match": payload.loose_match,
        "scoring_rule": "round(得分/题量×100)，宽松模式近似 0.5 分",
        "pool_source": "learned_first" if learned_ids else "random",
        "server_date": today_str(),
    }
    paper = ExamPaper(
        id=paper_id, user_id=user.id, chapter_id=chapter_id,
        config_json=json.dumps(config, ensure_ascii=False),
        questions_json=json.dumps(questions, ensure_ascii=False),
        answer_key_json=json.dumps(answer_key, ensure_ascii=False),
        status="active", started_at=now_str(), expires_at=expires.strftime("%Y-%m-%d %H:%M:%S"),
    )
    db.add(paper)
    await db.flush()
    return {
        "paper_id": paper_id,
        "expires_at": paper.expires_at,
        "config_snapshot": config,
        "questions": questions,
    }


async def report_blur(db: AsyncSession, user, paper_id: str, count: int) -> None:
    paper = (
        await db.execute(select(ExamPaper).where(ExamPaper.id == paper_id, ExamPaper.user_id == user.id))
    ).scalar_one_or_none()
    if paper is None:
        raise AppError("NOT_FOUND", "试卷不存在")
    paper.blur_count += count


async def submit_exam(db: AsyncSession, user, request: Request, payload) -> dict:
    idem_key = request.headers.get("Idempotency-Key", "")
    if not idem_key:
        raise AppError("VALIDATION_ERROR", "缺少 Idempotency-Key 请求头")
    replay = await find_replay(db, idem_key, "exam/submit", user.id, request)
    if replay is not None:
        return replay

    paper = (
        await db.execute(select(ExamPaper).where(ExamPaper.id == payload.paper_id, ExamPaper.user_id == user.id))
    ).scalar_one_or_none()
    if paper is None:
        raise AppError("NOT_FOUND", "试卷不存在")
    if paper.status == "submitted":
        raise AppError("EXAM_ALREADY_SUBMITTED", "该卷已交卷")

    config = json.loads(paper.config_json)
    key = json.loads(paper.answer_key_json)
    questions_json = json.loads(paper.questions_json)
    # qid → 正确选项 key（选项与释义的映射在下发题目里）
    correct_key_by_qid = {
        q["qid"]: next((o["key"] for o in q.get("payload", {}).get("options", []) if o["text"] == k["meaning"]), None)
        for q in questions_json
        for k in key
        if q["qid"] == k["qid"] and q["type"] == "choice"
    }
    loose = bool(config.get("loose_match"))
    answers = {a.get("qid"): a for a in payload.answers}

    per_question, wrong_word_ids, raw_scores = [], [], []
    stat_updates: list[tuple[int, str]] = []
    for k in key:
        a = answers.get(k["qid"])
        typed = (a or {}).get("typed")
        choice_key = (a or {}).get("choice_key")
        if k["type"] == "choice":
            # 考核选择题：客户端提交所选 key，服务端反查正确 key
            result = "correct" if choice_key and choice_key == correct_key_by_qid.get(k["qid"]) else "wrong"
        else:
            result = judge_mod.judge(typed or "", k["spelling"], loose)
        raw = question_raw(result, loose)
        raw_scores.append(raw)
        per_question.append({"qid": k["qid"], "word_id": k["word_id"], "type": k["type"], "result": result, "raw_score": raw})
        if result != "correct":
            wrong_word_ids.append(k["word_id"])
        stat_updates.append((k["word_id"], result))

    score, correct_rate = score_exam(raw_scores, loose)
    passed = score >= config.get("pass_score", 60)
    detail = {
        "per_question": per_question,
        "used_ms": payload.duration_ms,
        "server_date": today_str(),
    }

    # 批量落库：stat / wrongbook / daily / record / paper 状态
    now = now_str()
    for wid, result in stat_updates:
        stat, _ = await get_or_create_stat(db, user.id, wid)
        await finish_stat_event(db, user.id, stat, result, "exam", today_str(), full_sm2=False)
        await touch_wrongbook(db, user.id, wid, result, "exam")
    correct_n = sum(1 for r in raw_scores if r == 1.0)
    wrong_n = len(raw_scores) - correct_n
    await touch_daily(db, user.id, today_str(), correct=correct_n, wrong=wrong_n)

    record = ExamRecord(
        user_id=user.id, paper_id=paper.id, chapter_id=paper.chapter_id,
        score=score, duration=int(payload.duration_ms / 1000), blur_count=paper.blur_count,
        config_json=paper.config_json, detail_json=json.dumps(detail, ensure_ascii=False),
        idempotency_key=idem_key, created_at=now,
    )
    db.add(record)
    paper.status = "submitted"
    paper.submitted_at = now
    await db.flush()

    out = {
        "record_id": record.id,
        "score": score,
        "passed": passed,
        "correct_rate": round(correct_rate, 4),
        "duration": record.duration,
        "blur_count": paper.blur_count,
        "per_question": per_question,
        "wrong_word_ids": wrong_word_ids,
    }
    await save_snapshot(db, idem_key, "exam/submit", user.id, 200, out)
    return out


async def void_exam(db: AsyncSession, user, paper_id: str) -> None:
    paper = (
        await db.execute(select(ExamPaper).where(ExamPaper.id == paper_id, ExamPaper.user_id == user.id))
    ).scalar_one_or_none()
    if paper is None:
        raise AppError("NOT_FOUND", "试卷不存在")
    if paper.status == "active":
        paper.status = "void"


async def list_records(db: AsyncSession, user, page: int, page_size: int = 20) -> dict:
    stmt = select(ExamRecord).where(ExamRecord.user_id == user.id).order_by(ExamRecord.created_at.desc(), ExamRecord.id.desc())
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {
        "items": [
            {
                "id": r.id, "score": r.score, "duration": r.duration, "blur_count": r.blur_count,
                "config_snapshot": json.loads(r.config_json), "created_at": r.created_at,
            }
            for r in rows
        ],
        "total": total, "page": page, "page_size": page_size,
    }


async def get_record(db: AsyncSession, user, record_id: int) -> dict:
    r = (
        await db.execute(select(ExamRecord).where(ExamRecord.id == record_id, ExamRecord.user_id == user.id))
    ).scalar_one_or_none()
    if r is None:
        raise AppError("NOT_FOUND", "成绩不存在")
    return {
        "id": r.id, "score": r.score, "duration": r.duration, "blur_count": r.blur_count,
        "config_snapshot": json.loads(r.config_json), "detail": json.loads(r.detail_json),
        "created_at": r.created_at,
    }
