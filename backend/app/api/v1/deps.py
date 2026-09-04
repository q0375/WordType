"""API 依赖：get_current_user（JWT 四态 + 滑动续期头）、require_admin。"""

import time

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.errors import AppError
from ...core.security import create_token, decode_token
from ...db.engine import get_db
from ...models import User


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise AppError("AUTH_REQUIRED", "未认证")
    payload = decode_token(auth[7:])

    user = (await db.execute(select(User).where(User.id == int(payload["sub"])))).scalar_one_or_none()
    if user is None:
        raise AppError("AUTH_REQUIRED", "未认证")
    if payload.get("pv") != user.pwd_ver:
        raise AppError("AUTH_PWD_CHANGED", "密码已变更，请重新登录")
    if user.is_deleted:
        raise AppError("AUTH_DEACTIVATED", "账号已注销，30 天内可联系管理员恢复")

    # 滑动续期（§2.2/K2）：剩余 <24h → 响应附加 X-New-Token
    exp = payload.get("exp", 0)
    if exp - time.time() < 24 * 3600:
        request.state.new_token = create_token(user.id, user.username, user.role, user.pwd_ver)
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise AppError("ADMIN_REQUIRED", "需要管理员权限")
    return user
