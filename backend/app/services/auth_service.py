"""认证业务：注册（邀请码原子核销）、登录（锁定）、改密（pwd_ver+1）、JWT 四态校验。"""

from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import AppError
from ..core.security import create_token, hash_password, verify_password
from ..core import ratelimit
from ..models import DailySetting, InviteCode, User


def auth_payload(user: User) -> dict:
    return {
        "token": create_token(user.id, user.username, user.role, user.pwd_ver),
        "user": {"id": user.id, "username": user.username, "role": user.role},
    }


async def get_user_by_name(db: AsyncSession, username: str) -> User | None:
    return (
        await db.execute(select(User).where(User.username == username))
    ).scalar_one_or_none()


async def register(db: AsyncSession, username: str, password: str, invite_code: str | None, invite_required: bool, ip: str) -> dict:
    if not ratelimit.register_allowed(ip):
        raise AppError("RATE_LIMITED", "注册过于频繁，请稍后再试")
    if await get_user_by_name(db, username):
        raise AppError("USERNAME_TAKEN", "用户名已被占用")

    if invite_required:
        if not invite_code:
            raise AppError("INVITE_CODE_INVALID", "邀请码不能为空", [{"field": "invite_code", "issue": "missing"}])
        row = (
            await db.execute(select(InviteCode).where(InviteCode.code == invite_code))
        ).scalar_one_or_none()
        detail = None
        if row is None or not row.is_active or row.used_by is not None or row.expires_at <= datetime.now().strftime("%Y-%m-%d %H:%M:%S"):
            if row and row.used_by is not None:
                detail = [{"field": "invite_code", "issue": "used"}]
            elif row and row.expires_at <= datetime.now().strftime("%Y-%m-%d %H:%M:%S"):
                detail = [{"field": "invite_code", "issue": "expired"}]
            else:
                detail = [{"field": "invite_code", "issue": "invalid"}]
            raise AppError("INVITE_CODE_INVALID", "邀请码无效", detail)

    user = User(username=username, password_hash=hash_password(password), role="user")
    db.add(user)
    await db.flush()

    if invite_required:
        # 单语句原子核销，防一码多用（DBD §4.4）
        res = await db.execute(
            update(InviteCode)
            .where(
                InviteCode.code == invite_code,
                InviteCode.is_active == 1,
                InviteCode.used_by.is_(None),
                InviteCode.expires_at > datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
            .values(used_by=user.id, used_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        if res.rowcount != 1:
            raise AppError("INVITE_CODE_INVALID", "邀请码无效")

    db.add(DailySetting(user_id=user.id))
    await db.flush()
    return auth_payload(user)


async def login(db: AsyncSession, username: str, password: str, ip: str) -> dict:
    user = await get_user_by_name(db, username)
    # 失败锁定不改响应（防探测），仅计数
    if user is None or not verify_password(password, user.password_hash):
        ratelimit.login_failed(username, ip)
        raise AppError("AUTH_INVALID", "用户名或密码错误")
    if user.is_deleted:
        raise AppError("AUTH_DEACTIVATED", "账号已注销，30 天内可联系管理员恢复")
    user.last_login_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return auth_payload(user)


async def change_password(db: AsyncSession, user: User, old_password: str, new_password: str) -> dict:
    if not verify_password(old_password, user.password_hash):
        raise AppError("AUTH_INVALID", "旧密码错误")
    user.password_hash = hash_password(new_password)
    user.pwd_ver += 1
    await db.flush()
    return auth_payload(user)  # 响应签发新 token（口径16）
