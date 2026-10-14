"""API 依赖：get_current_user（JWT 四态 + 滑动续期头）、require_admin。"""

import time
from datetime import datetime

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.errors import AppError
from ...core.security import create_token, decode_token, is_token_revoked
from ...db.engine import get_db
from ...models import User

# 在线状态节流标记（进程内）：user_id → 上次写 last_seen_at 的 monotonic 时间
_seen_mark: dict[int, float] = {}


def _bearer_payload(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise AppError("AUTH_REQUIRED", "未认证")
    payload = decode_token(auth[7:])
    if is_token_revoked(payload.get("jti")):
        raise AppError("AUTH_REQUIRED", "登录已失效，请重新登录")
    return payload


async def get_token_payload(request: Request) -> dict:
    """仅解析并校验 token（不查库），供登出等需要 jti 的端点使用。"""
    return _bearer_payload(request)


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    payload = _bearer_payload(request)

    user = (await db.execute(select(User).where(User.id == int(payload["sub"])))).scalar_one_or_none()
    if user is None:
        raise AppError("AUTH_REQUIRED", "未认证")
    if payload.get("pv") != user.pwd_ver:
        raise AppError("AUTH_PWD_CHANGED", "密码已变更，请重新登录")
    if user.is_deleted:
        raise AppError("AUTH_DEACTIVATED", "账号已注销，30 天内可联系管理员恢复")

    # 在线状态（2026-09-22）：认证请求节流刷新 last_seen_at（≥60s 才写，避免高频请求写放大）。
    # 提交必须在依赖里完成：部分 GET 端点自身不 commit，依赖里不提交会丢失更新。
    now_ts = time.time()
    if now_ts - _seen_mark.get(user.id, 0.0) >= 60:
        _seen_mark[user.id] = now_ts
        if len(_seen_mark) > 4096:
            _seen_mark.clear()
        user.last_seen_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await db.commit()

    # 滑动续期（§2.2/K2）：剩余 <24h → 响应附加 X-New-Token
    exp = payload.get("exp", 0)
    if exp - time.time() < 24 * 3600:
        request.state.new_token = create_token(user.id, user.username, user.role, user.pwd_ver)
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise AppError("ADMIN_REQUIRED", "需要管理员权限")
    return user
