"""认证 + 设置 端点。"""

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ....db.engine import get_db
from ....models import User
from ....schemas import LoginIn, PasswordIn, RegisterIn, SettingsIn
from ....services import auth_service, misc_service
from ..deps import get_current_user

router = APIRouter()


@router.post("/auth/register")
async def register(payload: RegisterIn, request: Request, db: AsyncSession = Depends(get_db)):
    from ....core.config import get_settings

    ip = request.client.host if request.client else "unknown"
    data = await auth_service.register(db, payload.username, payload.password, payload.invite_code, get_settings().invite_required, ip)
    await db.commit()
    return data


@router.post("/auth/login")
async def login(payload: LoginIn, request: Request, db: AsyncSession = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"
    data = await auth_service.login(db, payload.username, payload.password, ip)
    await db.commit()
    return data


@router.post("/auth/logout", status_code=204)
async def logout(_: User = Depends(get_current_user)):
    return Response(status_code=204)


@router.post("/auth/password")
async def change_password(payload: PasswordIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    data = await auth_service.change_password(db, user, payload.old_password, payload.new_password)
    await db.commit()
    return data


@router.post("/account/deactivate", status_code=204)
async def deactivate(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from datetime import datetime

    user.is_deleted = 1
    user.deleted_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    await db.commit()
    return Response(status_code=204)


@router.get("/settings")
async def get_settings_ep(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await misc_service.get_settings_full(db, user)


@router.put("/settings")
async def put_settings(payload: SettingsIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    data = await misc_service.put_settings(db, user, payload)
    await db.commit()
    return data
