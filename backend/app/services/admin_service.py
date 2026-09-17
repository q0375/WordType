"""管理端业务：用户管理、邀请码、AI 配置（D28，AES-GCM）。"""

import json
import secrets
import string
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import AppError
from ..core.security import hash_password
from ..db.engine import now_str
from ..models import InviteCode, User

def _gen_code(n: int = 12) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def _gen_temp_password(n: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(n))
        if any(c.isdigit() for c in pw) and any(c.isalpha() for c in pw):
            return pw


async def list_users(db: AsyncSession, q: str | None, status: str, page: int, page_size: int = 20) -> dict:
    stmt = select(User)
    if q:
        stmt = stmt.where(User.username.contains(q))
    if status == "active":
        stmt = stmt.where(User.is_deleted == 0)
    elif status == "deleted":
        stmt = stmt.where(User.is_deleted == 1)
    stmt = stmt.order_by(User.id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {
        "items": [
            {
                "id": u.id, "username": u.username, "role": u.role, "is_deleted": bool(u.is_deleted),
                "deleted_at": u.deleted_at, "last_login_at": u.last_login_at, "created_at": u.created_at,
            }
            for u in rows
        ],
        "total": total, "page": page, "page_size": page_size,
    }


async def reset_password(db: AsyncSession, user_id: int) -> dict:
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if u is None:
        raise AppError("NOT_FOUND", "用户不存在")
    temp = _gen_temp_password()
    u.password_hash = hash_password(temp)
    u.pwd_ver += 1  # 口径16：全部旧会话失效
    return {"temp_password": temp}


async def restore_user(db: AsyncSession, user_id: int) -> None:
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if u is None or not u.is_deleted:
        raise AppError("NOT_FOUND", "用户不存在或未注销")
    if u.deleted_at:
        deleted = datetime.strptime(u.deleted_at, "%Y-%m-%d %H:%M:%S")
        if datetime.now() - deleted > timedelta(days=30):
            raise AppError("RESTORE_WINDOW_EXPIRED", "已超过 30 天恢复窗口")
    u.is_deleted = 0
    u.deleted_at = None


async def list_invite_codes(db: AsyncSession, status: str | None, page: int, page_size: int = 20) -> dict:
    stmt = select(InviteCode)
    if status == "active":
        stmt = stmt.where(InviteCode.is_active == 1, InviteCode.used_by.is_(None))
    elif status == "used":
        stmt = stmt.where(InviteCode.used_by.is_not(None))
    elif status == "disabled":
        stmt = stmt.where(InviteCode.is_active == 0)
    stmt = stmt.order_by(InviteCode.id.desc())
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {
        "items": [
            {
                "id": c.id, "code": c.code, "used_by": c.used_by, "used_at": c.used_at,
                "expires_at": c.expires_at, "is_active": bool(c.is_active), "created_at": c.created_at,
            }
            for c in rows
        ],
        "total": total, "page": page, "page_size": page_size,
    }


async def create_invite_codes(db: AsyncSession, admin, count: int, valid_days: int) -> list[str]:
    expires = (datetime.now() + timedelta(days=valid_days)).strftime("%Y-%m-%d %H:%M:%S")
    codes = []
    for _ in range(count):
        code = _gen_code()
        db.add(InviteCode(code=code, created_by=admin.id, expires_at=expires))
        codes.append(code)
    await db.flush()
    return codes


async def patch_invite_code(db: AsyncSession, code_id: int, is_active: int) -> None:
    c = (await db.execute(select(InviteCode).where(InviteCode.id == code_id))).scalar_one_or_none()
    if c is None:
        raise AppError("NOT_FOUND", "邀请码不存在")
    c.is_active = is_active
