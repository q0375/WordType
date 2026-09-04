"""错题本 / 统计 / 建议 / 导出 / 健康 端点。"""

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ....db.engine import get_db, get_sessionmaker
from ....models import User
from ....schemas import WrongBookIn, WrongBookPatchIn
from ....services import misc_service, tts_service
from ..deps import get_current_user

router = APIRouter()


@router.get("/tts")
async def tts(word: str, accent: str = "us"):
    """单词发音（微软 edge-tts）。匿名可访问：<audio> 标签无法携带 Authorization 头。"""
    return await tts_service.speak(word, accent)


@router.get("/wrongbook")
async def list_wrongbook(resolved: int = 0, source: str | None = None, pinned: int | None = None, page: int = 1, page_size: int = 50,
                         user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await misc_service.list_wrongbook(db, user, resolved, source, pinned, page, page_size)


@router.post("/wrongbook")
async def add_wrongbook(payload: WrongBookIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    data = await misc_service.add_wrongbook(db, user, payload.word_id)
    await db.commit()
    return data


@router.patch("/wrongbook/{word_id}", status_code=204)
async def patch_wrongbook(word_id: int, payload: WrongBookPatchIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await misc_service.patch_wrongbook(db, user, word_id, payload.pinned, payload.resolved)
    await db.commit()
    return Response(status_code=204)


@router.get("/stats/dashboard")
async def stats_dashboard(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await misc_service.dashboard(db, user)


@router.get("/stats/trend")
async def stats_trend(metric: str = "wpm", source: str = "all", days: int = 30,
                      user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return {"items": await misc_service.stats_trend(db, user, metric, source, days)}


@router.get("/advice")
async def get_advice(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    data = await misc_service.get_advice(db, user)
    await db.commit()
    return data


@router.post("/advice/refresh")
async def refresh_advice(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    data = await misc_service.refresh_advice(db, user)
    await db.commit()
    return data


@router.get("/export/account")
async def export_account(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    content = await misc_service.export_account_zip(db, user)
    await db.commit()
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="wordtype-account.zip"'},
    )
