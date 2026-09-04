"""学习 / 练习 / 复习 端点。"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ....db.engine import get_db
from ....models import User
from ....schemas import DictationIn, PracticeAnswerIn, PracticeSessionIn, ReviewAnswerIn, SelfRateIn
from ....services import practice_service, review_service, study_service
from ..deps import get_current_user

router = APIRouter()


@router.get("/study/next")
async def study_next(scope: str | None = None, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await study_service.study_next(db, user, scope)


@router.post("/study/self-rate")
async def self_rate(payload: SelfRateIn, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    data = await study_service.self_rate(db, user, payload.word_id, payload.rating, payload.request_id, request)
    await db.commit()
    return data


@router.post("/study/dictation")
async def dictation(payload: DictationIn, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    data = await study_service.dictation(db, user, payload.word_id, payload.typed, payload.duration_ms, payload.detail, payload.request_id, request)
    await db.commit()
    return data


@router.post("/study/position", status_code=204)
async def save_position(chapter_id: int, last_word_index: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await study_service.save_study_position(db, user.id, chapter_id, last_word_index)
    await db.commit()
    from fastapi import Response

    return Response(status_code=204)


@router.post("/practice/session")
async def practice_session(payload: PracticeSessionIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await practice_service.create_session(db, user, payload.chapter_ids, payload.types, payload.group_size)


@router.post("/practice/answer")
async def practice_answer(payload: PracticeAnswerIn, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    data = await practice_service.answer(db, user, payload, request)
    await db.commit()
    return data


@router.get("/review/today")
async def review_today(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    data = await review_service.review_today(db, user)
    await db.commit()
    return data


@router.post("/review/answer")
async def review_answer(payload: ReviewAnswerIn, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    data = await review_service.review_answer(db, user, payload, request)
    await db.commit()
    return data
