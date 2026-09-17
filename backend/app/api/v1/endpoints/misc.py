"""错题本 / 统计 / 建议 / 导出 / 健康 端点。"""

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.security import encrypt_secret
from ....db.engine import get_db, get_sessionmaker, now_str, today_str
from ....models import AdviceCache, User, UserAiConfig
from ....schemas import UserAiConfigIn, WrongBookIn, WrongBookPatchIn
from ....services import llm_advice, misc_service, tts_service
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


# ---------- 用户自有 AI（LLM）凭据 ----------


@router.get("/ai-config")
async def get_ai_config(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return llm_advice.mask(await llm_advice.load_user_config(db, user.id))


@router.put("/ai-config")
async def put_ai_config(payload: UserAiConfigIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    cfg = await llm_advice.load_user_config(db, user.id)
    if cfg is None:
        cfg = UserAiConfig(user_id=user.id)
        db.add(cfg)
    data = payload.model_dump(exclude_unset=True)
    if data.get("api_base_url") is not None:
        cfg.api_base_url = data["api_base_url"].strip()
    if data.get("model_name") is not None:
        cfg.model_name = data["model_name"].strip()
    if data.get("temperature") is not None:
        cfg.temperature = data["temperature"]
    if data.get("timeout_s") is not None:
        cfg.timeout_s = data["timeout_s"]
    if data.get("api_key"):  # 留空 = 不修改
        cfg.api_key_enc = encrypt_secret(data["api_key"])
    cfg.updated_at = now_str()
    await db.flush()
    # 凭据变更后当日建议缓存失效
    await db.execute(delete(AdviceCache).where(AdviceCache.user_id == user.id, AdviceCache.date == today_str()))
    await db.commit()
    return llm_advice.mask(cfg)


@router.post("/ai-config/test")
async def test_ai_config(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await llm_advice.test(db, user.id)


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
