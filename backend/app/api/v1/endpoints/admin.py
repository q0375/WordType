"""管理端点（全部 role=admin）。"""

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ....db.engine import get_db
from ....models import User
from ....schemas import AIConfigIn, InviteCodeIn, InviteCodePatchIn
from ....services import admin_service
from ..deps import require_admin

router = APIRouter()


@router.get("/admin/users")
async def list_users(q: str | None = None, status: str = "all", page: int = 1, page_size: int = 20,
                     _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await admin_service.list_users(db, q, status, page, page_size)


@router.post("/admin/users/{user_id}/reset-password")
async def reset_password(user_id: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    data = await admin_service.reset_password(db, user_id)
    await db.commit()
    return data


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


@router.get("/admin/ai-config")
async def get_ai_config(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await admin_service.get_ai_config(db)


@router.put("/admin/ai-config")
async def put_ai_config(payload: AIConfigIn, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    data = await admin_service.put_ai_config(db, payload)
    await db.commit()
    return data


@router.post("/admin/ai-config/test")
async def test_ai_config(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await admin_service.test_ai_config(db)
