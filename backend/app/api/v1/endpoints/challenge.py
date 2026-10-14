"""考核 / 游戏 端点。"""

from fastapi import APIRouter, Depends, Query, Request
from fastapi import Response as HTTPResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ....db.engine import get_db
from ....models import User
from ....schemas import BlurIn, ExamStartIn, ExamSubmitIn, GameStartIn, GameSubmitIn
from ....services import exam_service, game_service
from ..deps import get_current_user

router = APIRouter()


@router.post("/exam/start")
async def exam_start(payload: ExamStartIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    data = await exam_service.start_exam(db, user, payload)
    await db.commit()
    return data


@router.post("/exam/{paper_id}/blur", status_code=204)
async def exam_blur(paper_id: str, payload: BlurIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await exam_service.report_blur(db, user, paper_id, payload.count)
    await db.commit()
    return HTTPResponse(status_code=204)


@router.post("/exam/submit")
async def exam_submit(payload: ExamSubmitIn, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    data = await exam_service.submit_exam(db, user, request, payload)
    await db.commit()
    return data


@router.post("/exam/{paper_id}/void", status_code=204)
async def exam_void(paper_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await exam_service.void_exam(db, user, paper_id)
    await db.commit()
    return HTTPResponse(status_code=204)


@router.get("/exam/active")
async def exam_active(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """进行中的考核卷（供恢复作答）。"""
    return await exam_service.get_active_paper(db, user)


@router.get("/exam/assigned")
async def exam_assigned(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """本班级被分配且已到启用时间的考核卷列表。"""
    items = await exam_service.list_assigned_for_user(db, user)
    await db.commit()  # _expire_stale_papers 可能产生状态变更
    return {"items": items}


@router.post("/exam/assigned/{template_id}/start")
async def exam_assigned_start(template_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    data = await exam_service.start_assigned_exam(db, user, template_id)
    await db.commit()
    return data


@router.get("/exam/records")
async def exam_records(page: int = 1, page_size: int = 20, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await exam_service.list_records(db, user, page, page_size)


@router.get("/exam/records/{record_id}")
async def exam_record(record_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await exam_service.get_record(db, user, record_id)


@router.post("/game/start")
async def game_start(payload: GameStartIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    data = await game_service.start_game(db, user, payload)
    await db.commit()
    return data


@router.post("/game/submit")
async def game_submit(payload: GameSubmitIn, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    data = await game_service.submit_game(db, user, request, payload)
    await db.commit()
    return data


@router.get("/game/leaderboard")
async def game_leaderboard(
    book_id: int,
    mode: str = Query(..., pattern="^(endless|timed)$"),
    difficulty: str = Query(..., pattern="^(easy|normal|hard)$"),
    chapter_ids: str = Query("", description="逗号分隔章节 id；空=整本榜，单值=章节榜，多个=该组合专属榜"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """公共词库排行榜：按 词库+章节集合键(空=整本)+模式+难度 分组，每人取其最高分（前 10 + 自己前后 2 名）。"""
    from ....core.errors import AppError

    try:
        ids = [int(x) for x in chapter_ids.split(",") if x.strip()]
    except ValueError as e:
        raise AppError("VALIDATION_ERROR", "chapter_ids 格式非法（应为逗号分隔整数）") from e
    return await game_service.leaderboard(db, user, book_id, ids, mode, difficulty)
