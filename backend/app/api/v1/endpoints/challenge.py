"""考核 / 游戏 端点。"""

from fastapi import APIRouter, Depends, Request
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
