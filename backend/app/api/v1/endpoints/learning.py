"""学习 / 练习 / 复习 端点。"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ....db.engine import get_db
from ....models import User
from ....schemas import DictationIn, PracticeAnswerIn, PracticeSessionIn, ReviewAdvanceIn, ReviewAnswerIn, SelfRateIn
from ....services import practice_service, review_service, study_service
from ..deps import get_current_user

router = APIRouter()


@router.get("/study/next")
async def study_next(
    scope: str | None = None,
    force: bool = False,
    exclude: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """force=true 仅用于用户在完成页主动选择「重学一遍」，exclude=逗号分隔的词id（本轮已重学过）；
    默认全学过时返回 all_learned 而不推词。"""
    return await study_service.study_next(db, user, scope, force=force, exclude=exclude)


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
    return await practice_service.create_session(
        db, user, payload.chapter_ids, payload.types, payload.group_size, payload.word_ids or None
    )


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


@router.get("/review/plan")
async def review_plan(days: int = 14, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """未来复习计划：SM-2 按天算出的到期词（明天/后天/…），可提前复习。"""
    return await review_service.review_plan(db, user, days)


@router.post("/review/advance")
async def review_advance(payload: ReviewAdvanceIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """把某一天计划复习的词提前加入今日队列。"""
    data = await review_service.review_advance(db, user, payload.target_date)
    await db.commit()
    return data


@router.post("/review/answer")
async def review_answer(payload: ReviewAnswerIn, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    data = await review_service.review_answer(db, user, payload, request)
    await db.commit()
    return data
