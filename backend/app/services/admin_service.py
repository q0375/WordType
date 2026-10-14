"""管理端业务：用户管理、邀请码、AI 配置（D28，AES-GCM）。"""

import json
import secrets
import string
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import AppError
from ..core.security import hash_password
from ..db.engine import now_str
from ..models import ClassPaperAssign, ExamPaper, ExamRecord, ExamTemplate, InviteCode, StudyClass, User

def _gen_code(n: int = 12) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def _gen_temp_password(n: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(n))
        if any(c.isdigit() for c in pw) and any(c.isalpha() for c in pw):
            return pw


async def list_users(db: AsyncSession, q: str | None, status: str, page: int, page_size: int = 20) -> dict:
    from ..models import StudyClass

    stmt = select(User)
    if q:
        stmt = stmt.where(User.username.contains(q))
    if status == "active":
        stmt = stmt.where(User.is_deleted == 0)
    elif status == "deleted":
        stmt = stmt.where(User.is_deleted == 1)
    stmt = stmt.order_by(User.id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    classes = {c.id: c.name for c in (await db.execute(select(StudyClass))).scalars().all()}
    return {
        "items": [
            {
                "id": u.id, "username": u.username, "nickname": u.nickname, "role": u.role, "is_deleted": bool(u.is_deleted),
                "deleted_at": u.deleted_at, "last_login_at": u.last_login_at, "last_seen_at": u.last_seen_at, "created_at": u.created_at,
                "class_id": u.class_id, "class_name": classes.get(u.class_id, "默认班级"),
            }
            for u in rows
        ],
        "total": total, "page": page, "page_size": page_size,
    }


async def import_users(db: AsyncSession, admin: User, users: list) -> dict:
    """批量导入用户：用户名 + 密码（空则自动生成）+ 班级名（不存在则自动创建）。"""
    import re

    created, skipped = [], []
    existing = {
        u.username.lower(): u.is_deleted
        for u in (await db.execute(select(User))).scalars().all()
    }
    classes = {c.name: c.id for c in (await db.execute(select(StudyClass))).scalars().all()}
    batch_seen: set[str] = set()
    for idx, row in enumerate(users, start=1):
        username = row.username.strip()
        if not re.fullmatch(r"[a-zA-Z0-9_\u4e00-\u9fa5]{3,20}", username):
            skipped.append({"line": idx, "username": username, "reason": "用户名需为 3-20 位字母/数字/下划线/中文"})
            continue
        if username.lower() in existing or username.lower() in batch_seen:
            skipped.append({"line": idx, "username": username, "reason": "用户名已存在" + ("（已注销，需先恢复）" if existing.get(username.lower()) else "")})
            continue
        password_input = (row.password or "").strip()
        if password_input and (len(password_input) < 8 or not (re.search(r"[a-zA-Z]", password_input) and re.search(r"\d", password_input))):
            skipped.append({"line": idx, "username": username, "reason": "密码需至少 8 位且同时包含字母和数字"})
            continue
        password = password_input if password_input else _gen_temp_password()
        class_id = None
        class_name_resolved = None
        if row.class_name and row.class_name.strip():
            cname = row.class_name.strip()
            if cname not in classes:
                c = StudyClass(name=cname, created_by=admin.id)
                db.add(c)
                await db.flush()
                classes[cname] = c.id
            class_id = classes[cname]
            class_name_resolved = cname
        u = User(username=username, password_hash=await hash_password(password), role="user", class_id=class_id)
        db.add(u)
        await db.flush()
        batch_seen.add(username.lower())
        created.append({"username": username, "password": password, "auto_password": not password_input, "class_id": class_id, "class_name": class_name_resolved})
    return {"created": created, "skipped": skipped, "created_count": len(created), "skipped_count": len(skipped)}


async def reset_password(db: AsyncSession, user_id: int, new_password: str | None = None) -> dict:
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if u is None:
        raise AppError("NOT_FOUND", "用户不存在")
    if u.is_deleted:
        # 已注销账号不能重置密码（2026-09-21）：前端不展示入口，服务端兜底拒绝
        raise AppError("VALIDATION_ERROR", "该用户已注销，不能重置密码（如需启用请先恢复账号）")
    temp = new_password if new_password else _gen_temp_password()
    u.password_hash = await hash_password(temp)
    u.pwd_ver += 1  # 口径16：全部旧会话失效
    return {"temp_password": temp, "random": new_password is None}


async def restore_user(db: AsyncSession, user_id: int) -> None:
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if u is None or not u.is_deleted:
        raise AppError("NOT_FOUND", "用户不存在或未注销")
    if u.deleted_at:
        deleted = datetime.strptime(u.deleted_at, "%Y-%m-%d %H:%M:%S")
        if datetime.now() - deleted > timedelta(days=30):
            raise AppError("RESTORE_WINDOW_EXPIRED", "已超过 30 天恢复窗口")
    u.is_deleted = 0
    u.deleted_at = None


async def list_invite_codes(db: AsyncSession, status: str | None, page: int, page_size: int = 20) -> dict:
    stmt = select(InviteCode)
    if status == "active":
        stmt = stmt.where(InviteCode.is_active == 1, InviteCode.used_by.is_(None))
    elif status == "used":
        stmt = stmt.where(InviteCode.used_by.is_not(None))
    elif status == "disabled":
        stmt = stmt.where(InviteCode.is_active == 0)
    stmt = stmt.order_by(InviteCode.id.desc())
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {
        "items": [
            {
                "id": c.id, "code": c.code, "used_by": c.used_by, "used_at": c.used_at,
                "expires_at": c.expires_at, "is_active": bool(c.is_active), "created_at": c.created_at,
            }
            for c in rows
        ],
        "total": total, "page": page, "page_size": page_size,
    }


async def create_invite_codes(db: AsyncSession, admin, count: int, valid_days: int) -> list[str]:
    expires = (datetime.now() + timedelta(days=valid_days)).strftime("%Y-%m-%d %H:%M:%S")
    codes = []
    for _ in range(count):
        code = _gen_code()
        db.add(InviteCode(code=code, created_by=admin.id, expires_at=expires))
        codes.append(code)
    await db.flush()
    return codes


async def patch_invite_code(db: AsyncSession, code_id: int, is_active: int) -> None:
    c = (await db.execute(select(InviteCode).where(InviteCode.id == code_id))).scalar_one_or_none()
    if c is None:
        raise AppError("NOT_FOUND", "邀请码不存在")
    c.is_active = is_active


# ==================== 班级管理（D34） ====================


async def list_classes(db: AsyncSession) -> dict:
    classes = (await db.execute(select(StudyClass).order_by(StudyClass.is_default.desc(), StudyClass.id))).scalars().all()
    items = []
    for c in classes:
        member_count = (
            await db.execute(select(func.count()).select_from(User).where(User.class_id == c.id, User.is_deleted == 0))
        ).scalar_one()
        items.append({"id": c.id, "name": c.name, "is_default": bool(c.is_default), "member_count": member_count})
    return {"items": items}


async def create_class(db: AsyncSession, admin, name: str) -> dict:
    exists = (await db.execute(select(StudyClass).where(StudyClass.name == name))).scalar_one_or_none()
    if exists:
        raise AppError("CLASS_NAME_TAKEN", "同名班级已存在")
    c = StudyClass(name=name, created_by=admin.id)
    db.add(c)
    await db.flush()
    return {"id": c.id, "name": c.name, "is_default": False, "member_count": 0}


async def rename_class(db: AsyncSession, class_id: int, name: str) -> None:
    c = (await db.execute(select(StudyClass).where(StudyClass.id == class_id))).scalar_one_or_none()
    if c is None:
        raise AppError("NOT_FOUND", "班级不存在")
    dup = (await db.execute(select(StudyClass).where(StudyClass.name == name, StudyClass.id != class_id))).scalar_one_or_none()
    if dup:
        raise AppError("CLASS_NAME_TAKEN", "同名班级已存在")
    c.name = name


async def delete_class(db: AsyncSession, class_id: int) -> dict:
    c = (await db.execute(select(StudyClass).where(StudyClass.id == class_id))).scalar_one_or_none()
    if c is None:
        raise AppError("NOT_FOUND", "班级不存在")
    if c.is_default:
        raise AppError("DEFAULT_CLASS_PROTECTED", "默认班级不可删除")
    n = (
        await db.execute(select(func.count()).select_from(User).where(User.class_id == class_id, User.is_deleted == 0))
    ).scalar_one()
    if n > 0:
        raise AppError("CLASS_NOT_EMPTY", f"班级仍有 {n} 名成员，请先转移")
    await db.delete(c)
    return {"deleted": class_id}


async def set_user_class(db: AsyncSession, user_id: int, class_id: int | None) -> dict:
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if u is None:
        raise AppError("NOT_FOUND", "用户不存在")
    if class_id is None:
        default_cls = (await db.execute(select(StudyClass).where(StudyClass.is_default == 1))).scalars().first()
        if default_cls is None:
            raise AppError("NOT_FOUND", "默认班级缺失，请重启服务初始化")
        class_id = default_cls.id
    target = (await db.execute(select(StudyClass).where(StudyClass.id == class_id))).scalar_one_or_none()
    if target is None:
        raise AppError("NOT_FOUND", "目标班级不存在")
    u.class_id = class_id
    return {"user_id": u.id, "class_id": class_id, "class_name": target.name}


# ==================== 考核卷管理（D36：管理员定义、全班同卷） ====================


async def create_exam_template(db: AsyncSession, admin, payload) -> dict:
    from ..models import Chapter, Word, WordBook
    from ..services.exam_service import build_template_questions

    # 考核卷全班同卷：所有所选章节必须属于「公共词库」，私有库学生不可见，出不了卷
    # 多章节复选（2026-09-22）：chapter_ids 优先，chapter_id 兼容旧客户端
    chapter_ids = list(dict.fromkeys(payload.chapter_ids or ([] if payload.chapter_id is None else [payload.chapter_id])))
    if not chapter_ids:
        raise AppError("VALIDATION_ERROR", "请至少选择一个章节")
    rows = (
        await db.execute(
            select(Chapter, WordBook)
            .join(WordBook, WordBook.id == Chapter.book_id)
            .where(Chapter.id.in_(chapter_ids), Chapter.is_deleted == 0)
        )
    ).all()
    by_id = {c.id: (c, b) for c, b in rows}
    missing = [cid for cid in chapter_ids if cid not in by_id]
    if missing:
        raise AppError("NOT_FOUND", f"章节不存在：{'、'.join(f'#{i}' for i in missing)}")
    private = [f"#{cid}" for cid, (c, b) in by_id.items() if b.is_deleted or not b.is_public]
    if private:
        raise AppError("VALIDATION_ERROR", "考核卷只能基于公共词库生成：以下章节属于私有词库 " + "、".join(private))

    questions, answer_key = await build_template_questions(db, chapter_ids, payload.type_counts, payload.time_policy)
    config = {
        "chapter_id": chapter_ids[0],
        "chapter_ids": chapter_ids,
        "type_counts": payload.type_counts,
        "time_limit_min": payload.time_limit_min,
        "time_policy": payload.time_policy,
        "pass_score": payload.pass_score,
        # time_policy（2026-09-21 恢复）只作用于每题倒计时；计分恒按原始时限。
        # loose_match（2026-09-19）保持取消：得分比=词序相似度（difflib，连续值）
    }
    tpl = ExamTemplate(
        name=payload.name,
        chapter_id=chapter_ids[0],
        config_json=json.dumps(config, ensure_ascii=False),
        questions_json=json.dumps(questions, ensure_ascii=False),
        answer_key_json=json.dumps(answer_key, ensure_ascii=False),
        question_count=len(questions),
        created_by=admin.id,
    )
    db.add(tpl)
    await db.flush()
    return {"id": tpl.id, "name": tpl.name, "question_count": len(questions)}


async def list_exam_templates(db: AsyncSession) -> dict:
    from ..models import Chapter, ClassPaperAssign

    tpls = (await db.execute(select(ExamTemplate).order_by(ExamTemplate.id.desc()))).scalars().all()
    classes_map = {c.id: c.name for c in (await db.execute(select(StudyClass))).scalars()}
    items = []
    for t in tpls:
        cfg = json.loads(t.config_json)
        # 多章节（2026-09-22）：列表返回全部所选章节名；chapter_id/chapter_name 保留兼容（=首章）
        chapter_ids = [int(x) for x in (cfg.get("chapter_ids") or [t.chapter_id])]
        names_map = {
            c.id: c.name
            for c in (await db.execute(select(Chapter).where(Chapter.id.in_(chapter_ids)))).scalars()
        }
        chapter_names = [names_map.get(cid, f"#{cid}") for cid in chapter_ids]
        assigns = (
            await db.execute(
                select(ClassPaperAssign).where(ClassPaperAssign.template_id == t.id).order_by(ClassPaperAssign.id.desc())
            )
        ).scalars().all()
        items.append(
            {
                "id": t.id,
                "name": t.name,
                "chapter_id": t.chapter_id,
                "chapter_name": chapter_names[0] if chapter_names else None,
                "chapter_names": chapter_names,
                "question_count": t.question_count,
                "type_counts": cfg.get("type_counts"),
                "time_limit_min": cfg.get("time_limit_min"),
                "pass_score": cfg.get("pass_score", 60),
                "is_active": bool(t.is_active),
                "created_at": t.created_at,
                "assignments": [
                    {
                        "id": a.id,
                        "class_id": a.class_id,
                        "class_name": classes_map.get(a.class_id, f"#{a.class_id}"),
                        "available_at": a.available_at,
                        "expires_at": a.expires_at,
                        "max_attempts": a.max_attempts,
                    }
                    for a in assigns
                ],
            }
        )
    return {"items": items}


async def patch_exam_template(db: AsyncSession, template_id: int, payload) -> None:
    t = (await db.execute(select(ExamTemplate).where(ExamTemplate.id == template_id))).scalar_one_or_none()
    if t is None:
        raise AppError("NOT_FOUND", "考核卷不存在")
    if payload.name is not None:
        t.name = payload.name
    if payload.is_active is not None:
        t.is_active = 1 if payload.is_active else 0


async def delete_exam_template(db: AsyncSession, template_id: int) -> None:
    t = (await db.execute(select(ExamTemplate).where(ExamTemplate.id == template_id))).scalar_one_or_none()
    if t is None:
        raise AppError("NOT_FOUND", "考核卷不存在")
    await db.delete(t)  # 分配关系随之外键级联删除


async def assign_paper(db: AsyncSession, template_id: int, payload) -> dict:
    # 分配是管理端预置动作，与启用状态解耦：停用卷可先配置好分配关系，
    # 学生端能否开考仍由 start_assigned_exam 的 is_active 校验把守。
    t = (
        await db.execute(select(ExamTemplate).where(ExamTemplate.id == template_id))
    ).scalar_one_or_none()
    if t is None:
        raise AppError("NOT_FOUND", "考核卷不存在")
    cls = (await db.execute(select(StudyClass).where(StudyClass.id == payload.class_id))).scalar_one_or_none()
    if cls is None:
        raise AppError("NOT_FOUND", "班级不存在")
    dup = (
        await db.execute(
            select(ClassPaperAssign).where(
                ClassPaperAssign.template_id == template_id, ClassPaperAssign.class_id == payload.class_id
            )
        )
    ).scalar_one_or_none()
    if dup:
        raise AppError("ASSIGN_DUPLICATED", "该卷已分配给此班级")
    a = ClassPaperAssign(
        template_id=template_id,
        class_id=payload.class_id,
        available_at=payload.available_at,
        expires_at=payload.expires_at,
        max_attempts=payload.max_attempts,
    )
    db.add(a)
    await db.flush()
    return {"id": a.id}


async def delete_assignment(db: AsyncSession, assign_id: int) -> None:
    a = (await db.execute(select(ClassPaperAssign).where(ClassPaperAssign.id == assign_id))).scalar_one_or_none()
    if a is None:
        raise AppError("NOT_FOUND", "分配记录不存在")
    await db.delete(a)


# ==================== 考核卷成绩（管理员查看 / 导出） ====================


async def _template_or_404(db: AsyncSession, template_id: int) -> ExamTemplate:
    t = (await db.execute(select(ExamTemplate).where(ExamTemplate.id == template_id))).scalar_one_or_none()
    if t is None:
        raise AppError("NOT_FOUND", "考核卷不存在")
    return t


async def exam_template_questions(db: AsyncSession, template_id: int) -> dict:
    """查看考核卷题目（管理端预览）：questions + answer_key 按 qid 合并。"""
    t = await _template_or_404(db, template_id)
    cfg = json.loads(t.config_json)
    try:
        questions = json.loads(t.questions_json)
    except (ValueError, TypeError):
        questions = []
    try:
        answer_key = {a.get("qid"): a for a in json.loads(t.answer_key_json or "[]")}
    except (ValueError, TypeError):
        answer_key = {}
    items = []
    for i, q in enumerate(questions):
        k = answer_key.get(q.get("qid"), {})
        items.append(
            {
                "no": i + 1,
                "type": q.get("type"),
                "payload": q.get("payload") or {},
                "spelling": k.get("spelling"),
                "meaning": k.get("meaning"),
            }
        )
    return {
        "id": t.id,
        "name": t.name,
        "question_count": t.question_count,
        "type_counts": cfg.get("type_counts"),
        "items": items,
    }


async def exam_paper_results(db: AsyncSession, template_id: int) -> dict:
    """学生维度：每个参加者的总分、用时、失焦、尝试次数（以最高分为准，附最后一次）。"""
    t = await _template_or_404(db, template_id)
    cfg = json.loads(t.config_json)
    pass_score = cfg.get("pass_score", 60)
    # 该卷下所有用户的卷子
    papers = (
        await db.execute(
            select(ExamPaper).where(ExamPaper.template_id == template_id).order_by(ExamPaper.started_at.asc())
        )
    ).scalars().all()
    user_ids = sorted({p.user_id for p in papers})
    users = {u.id: u for u in (await db.execute(select(User).where(User.id.in_(user_ids)))).scalars()} if user_ids else {}
    classes = {c.id: c.name for c in (await db.execute(select(StudyClass))).scalars()}
    paper_ids = [p.id for p in papers]
    records = []
    if paper_ids:
        records = (
            await db.execute(
                select(ExamRecord).where(ExamRecord.paper_id.in_(paper_ids)).order_by(ExamRecord.id.asc())
            )
        ).scalars().all()
    recs_by_user: dict[int, list] = {}
    for r in records:
        recs_by_user.setdefault(r.user_id, []).append(r)

    items = []
    for uid in user_ids:
        rs = recs_by_user.get(uid, [])
        attempts = len(rs)  # 已交卷次数
        started_n = len([p for p in papers if p.user_id == uid])  # 开考次数（含作废/超时）
        if not rs:
            u = users.get(uid)
            items.append(
                {
                    "user_id": uid,
                    "username": u.username if u else f"#{uid}",
                    "class_name": classes.get(u.class_id, "-") if u else "-",
                    "attempts": attempts, "opened": started_n,
                    "best_score": None, "last_score": None, "best_record_id": None, "last_record_id": None,
                    "passed": None, "duration": None, "blur_count": None, "submitted_at": None,
                    "status": "未完成" if started_n else "未开始",
                }
            )
            continue
        best = max(rs, key=lambda r: (json.loads(r.detail_json or "{}").get("score_exact") or r.score, r.id))
        last = rs[-1]
        exact_best = json.loads(best.detail_json or "{}").get("score_exact")
        exact_last = json.loads(last.detail_json or "{}").get("score_exact")
        u = users.get(uid)
        items.append(
            {
                "user_id": uid,
                "username": u.username if u else f"#{uid}",
                "class_name": classes.get(u.class_id, "-") if u else "-",
                "attempts": attempts, "opened": started_n,
                "best_score": exact_best if exact_best is not None else best.score,
                "last_score": exact_last if exact_last is not None else last.score,
                "best_record_id": best.id, "last_record_id": last.id,
                "passed": best.score >= pass_score,
                "duration": best.duration, "blur_count": best.blur_count,
                "submitted_at": best.created_at,
                "status": "已交卷",
            }
        )
    scored = [i["best_score"] for i in items if i["best_score"] is not None]
    return {
        "template": {
            "id": t.id, "name": t.name, "question_count": t.question_count,
            "pass_score": pass_score, "time_limit_min": cfg.get("time_limit_min"),
            "type_counts": cfg.get("type_counts"),
        },
        "items": items,
        "summary": {
            "total_students": len(items),
            "submitted": len(scored),
            "average": round(sum(scored) / len(scored), 1) if scored else None,
            "max": max(scored) if scored else None,
            "min": min(scored) if scored else None,
            "pass_rate": round(len([s for s in scored if s >= pass_score]) / len(scored) * 100, 1) if scored else None,
        },
    }


async def admin_record_detail(db: AsyncSession, record_id: int) -> dict:
    """单个学生某次交卷的逐题明细（学生视角）。"""
    r = (await db.execute(select(ExamRecord).where(ExamRecord.id == record_id))).scalar_one_or_none()
    if r is None:
        raise AppError("NOT_FOUND", "成绩记录不存在")
    u = (await db.execute(select(User).where(User.id == r.user_id))).scalar_one_or_none()
    paper = (await db.execute(select(ExamPaper).where(ExamPaper.id == r.paper_id))).scalar_one_or_none()
    detail = json.loads(r.detail_json or "{}")
    tpl_id = paper.template_id if paper else None
    tpl = None
    if tpl_id:
        t = (await db.execute(select(ExamTemplate).where(ExamTemplate.id == tpl_id))).scalar_one_or_none()
        tpl = {"id": t.id, "name": t.name} if t else None
    return {
        "record_id": r.id,
        "user": {"id": r.user_id, "username": u.username if u else f"#{r.user_id}"},
        "paper": {"template": tpl, "source": paper.source if paper else None},
        "score": detail.get("score_exact", r.score),
        "duration": r.duration,
        "blur_count": r.blur_count,
        "created_at": r.created_at,
        "config_snapshot": json.loads(r.config_json),
        "per_question": detail.get("per_question", []),
    }


async def exam_paper_question_stats(db: AsyncSession, template_id: int) -> dict:
    """题目维度：每题的限时、得分率（平均）、正确/近似/错误人数分布。"""
    t = await _template_or_404(db, template_id)
    questions = json.loads(t.questions_json)
    paper_ids = (
        await db.execute(select(ExamPaper.id).where(ExamPaper.template_id == template_id))
    ).scalars().all()
    records = []
    if paper_ids:
        records = (await db.execute(select(ExamRecord).where(ExamRecord.paper_id.in_(paper_ids)))).scalars().all()
    # qid → 统计
    stat: dict[str, dict] = {}
    for q in questions:
        stat[q["qid"]] = {
            "qid": q["qid"], "type": q["type"],
            "limit_s": int(q.get("payload", {}).get("limit_s") or 0),
            "correct": 0, "near": 0, "wrong": 0, "ratio_sum": 0.0, "count": 0,
        }
    spell_by_qid: dict[str, str] = {}
    if questions:
        key = json.loads(t.answer_key_json)
        spell_by_qid = {k["qid"]: k["spelling"] for k in key}
    for r in records:
        for pq in json.loads(r.detail_json or "{}").get("per_question", []):
            s = stat.get(pq["qid"])
            if s is None:
                continue
            res = pq.get("result", "wrong")
            if res in ("correct", "near", "wrong"):
                s[res] += 1
            s["ratio_sum"] += float(pq.get("raw_score") or 0)
            s["count"] += 1
    items = []
    for i, q in enumerate(questions):
        s = stat[q["qid"]]
        c = s["count"]
        items.append(
            {
                "index": i + 1, "qid": q["qid"], "type": q["type"],
                "spelling": spell_by_qid.get(q["qid"], ""),
                "limit_s": s["limit_s"],
                "answered": c,
                "correct": s["correct"], "near": s["near"], "wrong": s["wrong"],
                "score_rate": round(s["ratio_sum"] / c * 100, 1) if c else None,
                "correct_rate": round(s["correct"] / c * 100, 1) if c else None,
            }
        )
    return {
        "template": {"id": t.id, "name": t.name, "question_count": t.question_count},
        "submissions": len(records),
        "items": items,
    }


async def question_answers(db: AsyncSession, template_id: int, qid: str) -> dict:
    """单题作答明细：该题每个学生（每次交卷）的实际作答、判定与得分比。"""
    t = await _template_or_404(db, template_id)
    questions = json.loads(t.questions_json)
    meta = next((q for q in questions if q["qid"] == qid), None)
    if meta is None:
        raise AppError("NOT_FOUND", "题目不存在（模板可能已重新组卷）")
    index = questions.index(meta) + 1
    key = next((k for k in json.loads(t.answer_key_json or "[]") if k.get("qid") == qid), None)
    if key is None:
        raise AppError("NOT_FOUND", "该题缺少答案键")
    correct_answer = key.get("spelling", "")
    if meta["type"] == "choice":
        # 选择题正确答案 = 选项文本与释义匹配的那一项（与交卷复核同口径）
        correct_answer = next(
            (o["text"] for o in meta.get("payload", {}).get("options", []) if o.get("text") == key.get("meaning")),
            key.get("meaning", ""),
        )
    paper_ids = (
        await db.execute(select(ExamPaper.id).where(ExamPaper.template_id == template_id))
    ).scalars().all()
    records = []
    if paper_ids:
        records = (
            await db.execute(
                select(ExamRecord).where(ExamRecord.paper_id.in_(paper_ids)).order_by(ExamRecord.id.asc())
            )
        ).scalars().all()
    user_ids = sorted({r.user_id for r in records})
    users = {u.id: u for u in (await db.execute(select(User).where(User.id.in_(user_ids)))).scalars()} if user_ids else {}
    attempt_of: dict[int, int] = {}
    answers = []
    for r in records:
        for pq in json.loads(r.detail_json or "{}").get("per_question", []):
            if pq.get("qid") != qid:
                continue
            attempt_of[r.user_id] = attempt_of.get(r.user_id, 0) + 1
            u = users.get(r.user_id)
            answers.append(
                {
                    "record_id": r.id,
                    "user_id": r.user_id,
                    "username": u.username if u else f"#{r.user_id}",
                    "attempt": attempt_of[r.user_id],
                    "your_answer": pq.get("your_answer"),
                    "result": pq.get("result", "wrong"),
                    "raw_score": pq.get("raw_score"),
                    "submitted_at": r.created_at,
                }
            )
    return {
        "question": {
            "qid": qid, "index": index, "type": meta["type"],
            "spelling": key.get("spelling", ""),
            "meaning": key.get("meaning", ""),
            "limit_s": int(meta.get("payload", {}).get("limit_s") or 0),
            "correct_answer": correct_answer,
        },
        "answers": answers,
    }


async def exam_paper_results_csv(db: AsyncSession, template_id: int) -> tuple[str, str]:
    """导出：学生成绩 CSV + 题目分析 CSV 合并（两段）。返回 (filename, text)。"""
    res = await exam_paper_results(db, template_id)
    qs = await exam_paper_question_stats(db, template_id)
    tinfo = res["template"]

    def esc(v) -> str:
        s = "" if v is None else str(v)
        return '"' + s.replace('"', '""') + '"'

    lines = [f"考核卷,{esc(tinfo['name'])},题量,{tinfo['question_count']},及格线,{tinfo['pass_score']}"]
    s = res["summary"]
    lines.append(f"参加人数,{s['total_students']},已交卷,{s['submitted']},平均分,{s['average']},最高,{s['max']},最低,{s['min']},及格率(%),{s['pass_rate']}")
    lines.append("")
    lines.append("学号ID,用户名,班级,状态,最高分,最后一次得分,是否及格,用时(秒),失焦次数,交卷时间,已开考次数,已交卷次数")
    for i in res["items"]:
        lines.append(
            ",".join(
                [
                    esc(i["user_id"]), esc(i["username"]), esc(i["class_name"]), esc(i["status"]),
                    esc(i["best_score"]), esc(i["last_score"]),
                    "" if i["passed"] is None else ("是" if i["passed"] else "否"),
                    esc(i["duration"]), esc(i["blur_count"]), esc(i["submitted_at"]),
                    esc(i["opened"]), esc(i["attempts"]),
                ]
            )
        )
    lines.append("")
    lines.append("【题目分析】 submissions=" + str(qs["submissions"]))
    lines.append("题号,题型,单词,限时(秒),答题人数,平均得分比(%),正确率(%),正确人数,近似人数,错误人数")
    for q in qs["items"]:
        lines.append(
            ",".join(
                [
                    esc(q["index"]), esc(q["type"]), esc(q["spelling"]), esc(q["limit_s"]),
                    esc(q["answered"]),
                    esc(q["score_rate"]), esc(q["correct_rate"]),
                    esc(q["correct"]), esc(q["near"]), esc(q["wrong"]),
                ]
            )
        )
    safe = "".join(ch for ch in tinfo["name"] if ch not in '\\/:*?"<>|').strip() or "paper"
    return f"exam-results-{tinfo['id']}-{safe}.csv", "\ufeff" + "\n".join(lines)
