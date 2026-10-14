"""管理端点（全部 role=admin）。"""

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ....db.engine import get_db
from ....models import User
from ....schemas import (
    ClassIn,
    ClassPatchIn,
    DevDataSeedIn,
    ExamTemplateIn,
    ExamTemplatePatchIn,
    GameFallMultIn,
    InviteCodeIn,
    InviteCodePatchIn,
    PaperAssignIn,
    ResetPasswordIn,
    UserClassIn,
    UserImportIn,
)
from ....services import admin_service, devdata_service, system_config
from ..deps import require_admin

router = APIRouter()


@router.get("/admin/users")
async def list_users(q: str | None = None, status: str = "all", page: int = 1, page_size: int = 20,
                     _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await admin_service.list_users(db, q, status, page, page_size)


@router.post("/admin/users/import")
async def import_users(payload: UserImportIn, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    data = await admin_service.import_users(db, admin, payload.users)
    await db.commit()
    return data


@router.post("/admin/users/{user_id}/reset-password")
async def reset_password(
    user_id: int,
    payload: ResetPasswordIn | None = None,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    # body 缺省/未传 new_password = 随机生成；传了 = 重置为指定密码（规则由 schema 校验）
    data = await admin_service.reset_password(db, user_id, payload.new_password if payload else None)
    await db.commit()
    return data


@router.put("/admin/game-config/fall-mult")
async def set_game_fall_mult(payload: GameFallMultIn, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """全局游戏下坠倍数（出现 → 消失 = 时限 × mult），随 /settings 下发，即时生效。"""
    await system_config.set_game_fall_mult(db, payload.mult)
    await db.commit()
    return {"game_fall_duration_mult": payload.mult}


@router.post("/admin/users/{user_id}/restore", status_code=204)
async def restore_user(user_id: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    await admin_service.restore_user(db, user_id)
    await db.commit()
    return Response(status_code=204)


@router.get("/admin/invite-codes")
async def list_invite_codes(status: str | None = None, page: int = 1, page_size: int = 20,
                            _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await admin_service.list_invite_codes(db, status, page, page_size)


@router.post("/admin/invite-codes", status_code=201)
async def create_invite_codes(payload: InviteCodeIn, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    codes = await admin_service.create_invite_codes(db, admin, payload.count, payload.valid_days)
    await db.commit()
    return {"codes": codes}


@router.patch("/admin/invite-codes/{code_id}", status_code=204)
async def patch_invite_code(code_id: int, payload: InviteCodePatchIn, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    await admin_service.patch_invite_code(db, code_id, payload.is_active)
    await db.commit()
    return Response(status_code=204)


@router.get("/admin/devdata/summary")
async def devdata_summary(user_id: int | None = None, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await devdata_service.get_summary(db, user_id, admin)


@router.post("/admin/devdata/seed")
async def devdata_seed(payload: DevDataSeedIn, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    data = await devdata_service.seed(db, admin, payload)
    await db.commit()
    return data


@router.post("/admin/devdata/clear")
async def devdata_clear(payload: DevDataSeedIn, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    data = await devdata_service.clear_all(db, admin, payload.target_user_id)
    await db.commit()
    return data


# ==================== 班级管理（D34） ====================


@router.get("/admin/classes")
async def list_classes(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await admin_service.list_classes(db)


@router.post("/admin/classes", status_code=201)
async def create_class(payload: ClassIn, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    data = await admin_service.create_class(db, admin, payload.name)
    await db.commit()
    return data


@router.patch("/admin/classes/{class_id}", status_code=204)
async def rename_class(class_id: int, payload: ClassPatchIn, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    await admin_service.rename_class(db, class_id, payload.name)
    await db.commit()
    return Response(status_code=204)


@router.delete("/admin/classes/{class_id}")
async def delete_class(class_id: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    data = await admin_service.delete_class(db, class_id)
    await db.commit()
    return data


@router.patch("/admin/users/{user_id}/class")
async def set_user_class(user_id: int, payload: UserClassIn, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    data = await admin_service.set_user_class(db, user_id, payload.class_id)
    await db.commit()
    return data


# ==================== 考核卷管理（D36） ====================


@router.get("/admin/exam-papers")
async def list_exam_templates(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await admin_service.list_exam_templates(db)


@router.post("/admin/exam-papers", status_code=201)
async def create_exam_template(payload: ExamTemplateIn, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    data = await admin_service.create_exam_template(db, admin, payload)
    await db.commit()
    return data


@router.patch("/admin/exam-papers/{template_id}", status_code=204)
async def patch_exam_template(template_id: int, payload: ExamTemplatePatchIn, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    await admin_service.patch_exam_template(db, template_id, payload)
    await db.commit()
    return Response(status_code=204)


@router.delete("/admin/exam-papers/{template_id}", status_code=204)
async def delete_exam_template(template_id: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    await admin_service.delete_exam_template(db, template_id)
    await db.commit()
    return Response(status_code=204)


@router.post("/admin/exam-papers/{template_id}/assign", status_code=201)
async def assign_paper(template_id: int, payload: PaperAssignIn, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    data = await admin_service.assign_paper(db, template_id, payload)
    await db.commit()
    return data


@router.get("/admin/exam-templates/{template_id}/questions")
async def exam_template_questions(template_id: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """查看考核卷题目（管理端预览）：题干 + 答案按 qid 合并。"""
    return await admin_service.exam_template_questions(db, template_id)


@router.get("/admin/exam-papers/{template_id}/results")
async def exam_paper_results(template_id: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """学生维度：每人的总分/及格/用时/失焦/尝试次数（默认取最高分那次）。"""
    return await admin_service.exam_paper_results(db, template_id)


@router.get("/admin/exam-papers/{template_id}/question-stats")
async def exam_paper_question_stats(template_id: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """题目维度：每题限时、平均得分比、正确率与三档人数分布。"""
    return await admin_service.exam_paper_question_stats(db, template_id)


@router.get("/admin/exam-papers/{template_id}/questions/{qid}/answers")
async def exam_question_answers(template_id: int, qid: str, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """单题作答明细：该题每个学生（每次交卷）的实际作答、判定与得分比。"""
    return await admin_service.question_answers(db, template_id, qid)


@router.get("/admin/exam-papers/{template_id}/results.csv")
async def exam_paper_results_csv(template_id: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """导出 CSV：学生成绩表 + 题目分析表（同一文件两段，带 BOM 供 Excel 直开）。"""
    filename, text = await admin_service.exam_paper_results_csv(db, template_id)
    # 文件名含中文：HTTP 头只能 latin-1，需用 RFC 5987 的 filename* 传送 UTF-8 名
    from urllib.parse import quote

    ascii_name = f"exam-results-{template_id}.csv"
    disp = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=text.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": disp},
    )


@router.delete("/admin/assigns/{assign_id}", status_code=204)
async def delete_assignment(assign_id: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    await admin_service.delete_assignment(db, assign_id)
    await db.commit()
    return Response(status_code=204)


@router.get("/admin/exam-records/{record_id}")
async def admin_exam_record_detail(record_id: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """某个学生某次交卷的逐题明细。"""
    return await admin_service.admin_record_detail(db, record_id)
