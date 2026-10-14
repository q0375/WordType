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
from ..domain.exam_scoring import score_exam
from ..domain.exam_timing import (
    EXAM_TIME_POLICY_DEFAULT,
    question_base_limit_s,
    question_limit_s,
    time_policy_factor,
)
from ..models import (
    ClassPaperAssign,
    ExamPaper,
    ExamRecord,
    ExamTemplate,
    StudyClass,
    UserWordStat,
    Word,
)
from .stat_ops import finish_stat_event, get_or_create_stat, touch_daily, touch_wrongbook


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
            masked = f"{w.spelling[0]}{'_' * (len(w.spelling) - 1)}"  # 回退：首字母提示
        return {"sentence_masked": masked, "answer_len": len(w.spelling)}
    if qtype == "choice":
        distractors = [m for m in (all_meanings or []) if m != w.meaning]
        ds = random.sample(distractors, min(3, len(distractors)))
        options = [{"key": k, "text": t} for k, t in zip("ABCD", random.sample(ds + [w.meaning], len(ds) + 1))]
        # 缺音标时回退给单词本身，避免题干空白
        return {"phonetic": w.phonetic or w.spelling, "options": options}
    return {}


def _inject_limits(
    questions: list[dict],
    key: list[dict],
    overrides: dict[int, int | None] | None = None,
    policy: str = EXAM_TIME_POLICY_DEFAULT,
) -> None:
    """按词限时（D35）：把每题「倒计时 limit_s」与「计分权重 base_limit_s」写进下发题目 payload。

    base_limit_s = 原始时限（覆盖或默认公式），交卷计分权重用它；
    limit_s = 实际倒计时 = ceil(base_limit_s × 档位倍数)——档位（2026-09-21 恢复）只改倒计时，
    计分恒按原始时限（用户定稿）；overrides 为 word_id → 管理员设置的基础秒数。
    """
    spell_by_qid = {k["qid"]: k["spelling"] for k in key}
    word_by_qid = {k["qid"]: k["word_id"] for k in key}
    overrides = overrides or {}
    for q in questions:
        qid = q["qid"]
        ov = overrides.get(word_by_qid.get(qid))
        spelling = spell_by_qid.get(qid, "")
        payload = q.setdefault("payload", {})
        payload["base_limit_s"] = round(question_base_limit_s(q["type"], spelling, ov), 4)
        payload["limit_s"] = question_limit_s(q["type"], spelling, ov, policy)


async def _expire_stale_papers(db: AsyncSession, user_id: int) -> None:
    """自动作废已超时未交卷的进行中卷子（修复 EXAM_ALREADY_ACTIVE 残留）。"""
    papers = (
        await db.execute(
            select(ExamPaper).where(ExamPaper.user_id == user_id, ExamPaper.status == "active")
        )
    ).scalars().all()
    now = now_str()
    for p in papers:
        if p.expires_at <= now:
            p.status = "expired"


async def get_active_paper(db: AsyncSession, user) -> dict | None:
    """进行中的卷子（供前端恢复作答）。"""
    await _expire_stale_papers(db, user.id)
    paper = (
        await db.execute(
            select(ExamPaper).where(ExamPaper.user_id == user.id, ExamPaper.status == "active")
        )
    ).scalars().first()
    if paper is None:
        return None
    expires = datetime.strptime(paper.expires_at, "%Y-%m-%d %H:%M:%S")
    remaining = max(0, int((expires - datetime.now()).total_seconds()))
    return {
        "paper_id": paper.id,
        "source": paper.source,
        "template_id": paper.template_id,
        "expires_at": paper.expires_at,
        "remaining_s": remaining,
        "config_snapshot": json.loads(paper.config_json),
        "questions": json.loads(paper.questions_json),
    }


async def start_exam(db: AsyncSession, user, payload) -> dict:
    await _expire_stale_papers(db, user.id)
    active = (
        await db.execute(
            select(ExamPaper).where(ExamPaper.user_id == user.id, ExamPaper.status == "active")
        )
    ).scalars().first()
    if active is not None:
        raise AppError("EXAM_ALREADY_ACTIVE", "已有进行中的考核卷，可先继续作答或放弃该卷")

    # 多章节复选（2026-09-22）：chapter_ids 优先，chapter_id 兼容旧客户端（单章节）
    chapter_ids = list(dict.fromkeys(payload.chapter_ids or ([] if payload.chapter_id is None else [payload.chapter_id])))
    if not chapter_ids:
        raise AppError("VALIDATION_ERROR", "请至少选择一个章节")
    pool = list(
        (
            await db.execute(select(Word).where(Word.chapter_id.in_(chapter_ids), Word.is_deleted == 0).order_by(Word.id))
        ).scalars()
    )
    if not pool:
        raise AppError("EMPTY_WORD_POOL", "所选章节没有单词")
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
    if total > len(pool):
        raise AppError("EMPTY_WORD_POOL", f"题目总数 {total} 超过所选章节合计词数 {len(pool)}，请减少题量")
    ordered = learned_ids + rest
    if len(ordered) < total:
        raise AppError("EMPTY_WORD_POOL", "所选章节词量不足以组卷")

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
        "chapter_id": chapter_ids[0],
        "chapter_ids": chapter_ids,
        "type_counts": payload.type_counts,
        "time_limit_min": payload.time_limit_min,
        "time_policy": payload.time_policy,
        "pass_score": payload.pass_score,
        # 每词限时档位（宽松/标准/紧凑）2026-09-21 恢复：倍数只作用于每题倒计时，
        # 计分恒按原始时限 base_limit_s（档位不影响卷面分）。
        # 宽松判定已于 2026-09-19 取消：得分比 = 序列相似度（连续值），
        # 不再有卷级 loose_match 开关；旧卷 config 里若残留该键，交卷时一律忽略。
        "scoring_rule": "Σ(得分比×原始时限)/Σ(原始时限)×100，得分比=词序相似度（difflib，连续值）",
        "pool_source": "learned_first" if learned_ids else "random",
        "server_date": today_str(),
    }
    _inject_limits(questions, answer_key, {wid: w.time_limit_s for wid, w in words_by_id.items()}, payload.time_policy)
    paper = ExamPaper(
        id=paper_id, user_id=user.id, chapter_id=chapter_ids[0],
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
    answers = {a.get("qid"): a for a in payload.answers}
    option_text = {
        q["qid"]: {o["key"]: o["text"] for o in q.get("payload", {}).get("options", [])}
        for q in questions_json
    }
    # 计分权重：原始时限 base_limit_s；兼容旧卷：无 base_limit_s 时回落到 limit_s
    def _weight_of(q: dict) -> float:
        p = q.get("payload", {}) or {}
        v = p.get("base_limit_s")
        if v is None:
            v = p.get("limit_s") or 0
        return float(v)

    weight_by_qid = {q["qid"]: _weight_of(q) for q in questions_json}

    per_question, wrong_word_ids, raw_scores, limit_weights = [], [], [], []
    # (word_id, result, answered)：answered=False = 未作答（提前交卷/放弃），不进错题本（2026-10-03）
    stat_updates: list[tuple[int, str, bool]] = []
    for k in key:
        a = answers.get(k["qid"])
        typed = (a or {}).get("typed")
        choice_key = (a or {}).get("choice_key")
        if k["type"] == "choice":
            # 考核选择题：客户端提交所选 key，服务端反查正确 key
            ck = correct_key_by_qid.get(k["qid"])
            ok = bool(choice_key) and choice_key == ck
            ratio = 1.0 if ok else 0.0
            result = "correct" if ok else "wrong"
            your_answer = option_text.get(k["qid"], {}).get(choice_key or "", "")
            correct_answer = option_text.get(k["qid"], {}).get(ck or "", k["meaning"])
        else:
            # 得分比 = 输入正确的字符数 ÷ 全部字符数（大小写敏感）；2026-09-19 起无「近似」档
            ratio = judge_mod.partial_ratio(typed or "", k["spelling"])
            result = "correct" if ratio >= 1.0 else "wrong"
            your_answer = typed or ""
            correct_answer = k["spelling"]
        raw_scores.append(ratio)
        limit_weights.append(weight_by_qid.get(k["qid"], 0.0))
        per_question.append(
            {
                "qid": k["qid"], "word_id": k["word_id"], "type": k["type"],
                "result": result, "raw_score": ratio,
                "limit_s": weight_by_qid.get(k["qid"], 0.0),
                "spelling": k["spelling"], "meaning": k["meaning"],
                "your_answer": your_answer, "correct_answer": correct_answer,
            }
        )
        if result != "correct":
            wrong_word_ids.append(k["word_id"])
        # 「没作答」与「答错」区分开：放弃/提前交卷时剩下的题不该污染错题本（成绩与统计口径不变）
        stat_updates.append((k["word_id"], result, a is not None))

    # 防泄题（2026-09-23）：管理员发布的考核（template 卷）在交卷响应与落库明细中
    # 不返回题目与答案，防止先做完的学生截图传题；个人自组卷保留完整对错详情供自查。
    if paper.source == "template":
        per_question = [
            {k: v for k, v in q.items() if k not in ("spelling", "meaning", "your_answer", "correct_answer")}
            for q in per_question
        ]

    score, correct_rate = score_exam(raw_scores, limit_weights)
    passed = score >= config.get("pass_score", 60)
    detail = {
        "per_question": per_question,
        "used_ms": payload.duration_ms,
        "score_exact": score,
        "server_date": today_str(),
    }

    # 批量落库：stat / wrongbook / daily / record / paper 状态
    now = now_str()
    for wid, result, answered in stat_updates:
        stat, _ = await get_or_create_stat(db, user.id, wid)
        await finish_stat_event(db, user.id, stat, result, "exam", today_str(), full_sm2=False)
        if answered:  # 未作答不计入错题本（2026-10-03）
            await touch_wrongbook(db, user.id, wid, result, "exam")
    correct_n = sum(1 for r in per_question if r["result"] == "correct")
    wrong_n = len(raw_scores) - correct_n
    await touch_daily(db, user.id, today_str(), correct=correct_n, wrong=wrong_n)

    record = ExamRecord(
        user_id=user.id, paper_id=paper.id, chapter_id=paper.chapter_id,
        # 列仍是 Integer：存四舍五入整数，精确分（含小数）落在 detail.score_exact
        score=int(round(score)), duration=int(payload.duration_ms / 1000), blur_count=paper.blur_count,
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
    stmt = (
        select(ExamRecord, ExamPaper.source)
        .outerjoin(ExamPaper, ExamPaper.id == ExamRecord.paper_id)
        .where(ExamRecord.user_id == user.id)
        .order_by(ExamRecord.created_at.desc(), ExamRecord.id.desc())
    )
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.offset((page - 1) * page_size).limit(page_size))).all()
    return {
        "items": [
            {
                "id": r.id, "score": r.score, "duration": r.duration, "blur_count": r.blur_count,
                "config_snapshot": json.loads(r.config_json), "created_at": r.created_at,
                # 来源：template=管理员下发卷 / self=自组卷（前端据此区分两类成绩）
                "source": src or "self",
            }
            for r, src in rows
        ],
        "total": total, "page": page, "page_size": page_size,
    }


async def get_record(db: AsyncSession, user, record_id: int) -> dict:
    row = (
        await db.execute(
            select(ExamRecord, ExamPaper.source)
            .outerjoin(ExamPaper, ExamPaper.id == ExamRecord.paper_id)
            .where(ExamRecord.id == record_id, ExamRecord.user_id == user.id)
        )
    ).first()
    if row is None:
        raise AppError("NOT_FOUND", "成绩不存在")
    r, src = row
    return {
        "id": r.id, "score": r.score, "duration": r.duration, "blur_count": r.blur_count,
        "config_snapshot": json.loads(r.config_json), "detail": json.loads(r.detail_json),
        "created_at": r.created_at,
        "source": src or "self",
    }


# ==================== 考核卷（管理员定义，全班同卷） ====================


async def build_template_questions(
    db: AsyncSession,
    chapter_ids: int | list[int],
    type_counts: dict[str, int],
    time_policy: str = EXAM_TIME_POLICY_DEFAULT,
) -> tuple[list[dict], list[dict]]:
    """为考核卷模板固定组卷（不含已学优先——同卷要求所有参与者完全一致，纯随机）。

    chapter_ids 支持单章节（int）或多章节复选（list，2026-09-22）；
    time_policy（宽松/标准/紧凑）只改每题倒计时；计分恒按原始时限。
    """
    if isinstance(chapter_ids, int):
        chapter_ids = [chapter_ids]
    pool = list(
        (await db.execute(select(Word).where(Word.chapter_id.in_(chapter_ids), Word.is_deleted == 0).order_by(Word.id))).scalars()
    )
    if not pool:
        raise AppError("EMPTY_WORD_POOL", "所选章节没有单词")
    total = sum(type_counts.values())
    if total <= 0:
        raise AppError("VALIDATION_ERROR", "题量必须大于 0")
    if len(pool) < total:
        raise AppError("EMPTY_WORD_POOL", f"所选章节合计词量不足：仅 {len(pool)} 词，需要 {total} 题")
    random.shuffle(pool)
    words_by_id = {w.id: w for w in pool}
    all_meanings = [w.meaning for w in pool]
    questions, answer_key = [], []
    idx = 0
    for qtype, count in type_counts.items():
        for _ in range(count):
            if idx >= len(pool):
                break
            w = pool[idx]
            idx += 1
            qid = uuid.uuid4().hex
            wid_ = w.id
            questions.append({"qid": qid, "type": qtype, "word_id": wid_, "payload": _payload_for(w, qtype, all_meanings)})
            answer_key.append({"qid": qid, "word_id": wid_, "type": qtype, "spelling": w.spelling, "meaning": w.meaning})
    _inject_limits(questions, answer_key, {wid: w.time_limit_s for wid, w in words_by_id.items()}, time_policy)
    return questions, answer_key


async def list_assigned_for_user(db: AsyncSession, user) -> list[dict]:
    """当前用户班级可参加的考核卷（到时自动启用、过期自动隐藏），并标注成绩状态。"""
    await _expire_stale_papers(db, user.id)
    now = now_str()
    class_id = user.class_id
    if class_id is None:
        default_cls = (await db.execute(select(StudyClass).where(StudyClass.is_default == 1))).scalars().first()
        class_id = default_cls.id if default_cls else None
    if class_id is None:
        return []
    rows = (
        await db.execute(
            select(ExamTemplate, ClassPaperAssign)
            .join(ClassPaperAssign, ClassPaperAssign.template_id == ExamTemplate.id)
            .where(
                ClassPaperAssign.class_id == class_id,
                ExamTemplate.is_active == 1,
                (ClassPaperAssign.available_at.is_(None)) | (ClassPaperAssign.available_at <= now),
                (ClassPaperAssign.expires_at.is_(None)) | (ClassPaperAssign.expires_at > now),
            )
            .order_by(ClassPaperAssign.available_at.desc().nullslast(), ExamTemplate.id.desc())
        )
    ).all()
    out = []
    for tpl, assign in rows:
        papers = (
            await db.execute(
                select(ExamPaper).where(ExamPaper.user_id == user.id, ExamPaper.template_id == tpl.id).order_by(ExamPaper.id.desc())
            )
        ).scalars().all()
        active_paper = next((p for p in papers if p.status == "active"), None)
        # 计次口径：开考即计次——除进行中的卷子外全部计入（交卷/放弃/超时均消耗次数）
        attempts_used = len([p for p in papers if p.status != "active"])
        done = next(
            (
                r
                for r in (
                    await db.execute(
                        select(ExamRecord)
                        .where(
                            ExamRecord.user_id == user.id,
                            ExamRecord.paper_id.in_(select(ExamPaper.id).where(ExamPaper.template_id == tpl.id, ExamPaper.user_id == user.id)),
                        )
                        .order_by(ExamRecord.id.desc())
                    )
                ).scalars()
            ),
            None,
        )
        cfg = json.loads(tpl.config_json)
        out.append(
            {
                "template_id": tpl.id,
                "name": tpl.name,
                "question_count": tpl.question_count,
                "type_counts": cfg.get("type_counts"),
                "time_limit_min": cfg.get("time_limit_min"),
                "available_at": assign.available_at,
                "expires_at": assign.expires_at,
                "max_attempts": assign.max_attempts,
                "attempts_used": attempts_used,
                "active_paper_id": active_paper.id if active_paper else None,
                "attempt": {"record_id": done.id, "score": done.score, "passed": done.score >= cfg.get("pass_score", 60)} if done else None,
            }
        )
    return out


async def start_assigned_exam(db: AsyncSession, user, template_id: int) -> dict:
    """开始一张考核卷：题目与所有人完全一致。"""
    await _expire_stale_papers(db, user.id)
    active = (
        await db.execute(select(ExamPaper).where(ExamPaper.user_id == user.id, ExamPaper.status == "active"))
    ).scalars().first()
    if active is not None:
        raise AppError("EXAM_ALREADY_ACTIVE", "已有进行中的考核卷，可先继续作答或放弃该卷")

    tpl = (await db.execute(select(ExamTemplate).where(ExamTemplate.id == template_id, ExamTemplate.is_active == 1))).scalar_one_or_none()
    if tpl is None:
        raise AppError("NOT_FOUND", "考核卷不存在或已停用")
    # 归属与生效校验
    now = now_str()
    class_id = user.class_id
    if class_id is None:
        default_cls = (await db.execute(select(StudyClass).where(StudyClass.is_default == 1))).scalars().first()
        class_id = default_cls.id if default_cls else None
    assign = (
        await db.execute(
            select(ClassPaperAssign).where(
                ClassPaperAssign.template_id == template_id,
                ClassPaperAssign.class_id == class_id,
                (ClassPaperAssign.available_at.is_(None)) | (ClassPaperAssign.available_at <= now),
                (ClassPaperAssign.expires_at.is_(None)) | (ClassPaperAssign.expires_at > now),
            )
        )
    ).scalar_one_or_none()
    if assign is None:
        raise AppError("FORBIDDEN", "该考核卷未对你的班级开放（未到启用时间或已截止）")

    # 可考次数限制：开考即计次——已消耗次数（交卷/放弃/超时）达到上限则拒绝
    if assign.max_attempts:
        used = (
            await db.execute(
                select(func.count())
                .select_from(ExamPaper)
                .where(
                    ExamPaper.user_id == user.id,
                    ExamPaper.template_id == template_id,
                    ExamPaper.status != "active",
                )
            )
        ).scalar_one()
        if used >= assign.max_attempts:
            raise AppError("EXAM_ATTEMPT_LIMIT", f"该考核卷最多可考 {assign.max_attempts} 次，你已用完考试次数")

    questions = json.loads(tpl.questions_json)
    answer_key = json.loads(tpl.answer_key_json)
    cfg = json.loads(tpl.config_json)
    expires = datetime.now() + timedelta(minutes=cfg.get("time_limit_min", 60))
    paper = ExamPaper(
        id=uuid.uuid4().hex,
        user_id=user.id,
        chapter_id=tpl.chapter_id,
        template_id=tpl.id,
        source="template",
        config_json=json.dumps({**cfg, "template_name": tpl.name, "paper_ref": f"tpl:{tpl.id}"}, ensure_ascii=False),
        questions_json=json.dumps(questions, ensure_ascii=False),
        answer_key_json=json.dumps(answer_key, ensure_ascii=False),
        status="active",
        started_at=now_str(),
        expires_at=expires.strftime("%Y-%m-%d %H:%M:%S"),
    )
    db.add(paper)
    await db.flush()
    return {
        "paper_id": paper.id,
        "source": "template",
        "template_name": tpl.name,
        "expires_at": paper.expires_at,
        "config_snapshot": json.loads(paper.config_json),
        "questions": questions,
    }
