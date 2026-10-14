"""JWT（含 pwd_ver 校验四态）、bcrypt、AES-GCM（D28 key 加密）。"""

import base64
import uuid
from datetime import datetime, timedelta

import bcrypt
import jwt as pyjwt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from starlette.concurrency import run_in_threadpool

from .config import get_settings

ALG = "HS256"


# bcrypt 单次约 260ms 纯 CPU（rounds=12）。若直接在协程里同步调用，会顶住唯一的事件循环，
# 使**全站**请求排队（实测：20 人同时登录时，其他用户的轻量请求从 5ms 恶化到 1943ms）。
# 放进线程池：bcrypt（Rust 实现）会释放 GIL，2 核可并行，事件循环保持可调度。
def _hash_password_sync(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password_sync(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


async def hash_password(plain: str) -> str:
    """计算密码哈希（线程池执行，不阻塞事件循环）。"""
    return await run_in_threadpool(_hash_password_sync, plain)


async def verify_password(plain: str, hashed: str) -> bool:
    """校验密码（线程池执行，不阻塞事件循环）。"""
    return await run_in_threadpool(_verify_password_sync, plain, hashed)


def create_token(user_id: int, username: str, role: str, pwd_ver: int) -> str:
    s = get_settings()
    now = datetime.now()
    payload = {
        "sub": str(user_id),
        "name": username,
        "role": role,
        "pv": pwd_ver,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=s.jwt_expire_days)).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    return pyjwt.encode(payload, s.jwt_secret, algorithm=ALG)


def decode_token(token: str) -> dict:
    """签名→exp→由调用方比对 pv 与 is_deleted。失败抛 AppError。"""
    from .errors import AppError

    s = get_settings()
    try:
        return pyjwt.decode(token, s.jwt_secret, algorithms=[ALG])
    except pyjwt.ExpiredSignatureError:
        raise AppError("AUTH_EXPIRED", "登录已过期，请重新登录")
    except pyjwt.InvalidTokenError:
        raise AppError("AUTH_REQUIRED", "未认证")


# ---------- 登出失效（jti 黑名单） ----------
# 单进程 uvicorn 假设下用内存表即可；进程重启后黑名单清空，
# 但那时旧 token 仍需重新登录（前端已清 localStorage），影响可忽略。
_revoked_jti: dict[str, float] = {}  # jti -> exp 时间戳
_MAX_REVOKED = 20000


def revoke_token(jti: str | None, exp: float | None) -> None:
    """把某个 token 的 jti 加入黑名单，直到其自身过期。"""
    if not jti:
        return
    _revoked_jti[jti] = float(exp or 0)
    if len(_revoked_jti) > _MAX_REVOKED:
        now = datetime.now().timestamp()
        for k, v in list(_revoked_jti.items()):
            if v and v < now:
                _revoked_jti.pop(k, None)


def is_token_revoked(jti: str | None) -> bool:
    if not jti:
        return False
    exp = _revoked_jti.get(jti)
    if exp is None:
        return False
    if exp and exp < datetime.now().timestamp():
        _revoked_jti.pop(jti, None)
        return False
    return True


def encrypt_secret(plain: str) -> str:
    key = get_settings().aes_key.encode("utf-8")[:32]
    aes = AESGCM(key)
    nonce = uuid.uuid4().bytes[:12]
    ct = aes.encrypt(nonce, plain.encode("utf-8"), None)
    return base64.b64encode(nonce + ct).decode("utf-8")


def decrypt_secret(token: str) -> str:
    key = get_settings().aes_key.encode("utf-8")[:32]
    aes = AESGCM(key)
    raw = base64.b64decode(token)
    return aes.decrypt(raw[:12], raw[12:], None).decode("utf-8")


def mask_secret(plain_or_cipher: str, encrypted: bool = True) -> str:
    if not plain_or_cipher:
        return ""
    try:
        plain = decrypt_secret(plain_or_cipher) if encrypted else plain_or_cipher
    except Exception:
        plain = ""
    return f"****{plain[-4:]}" if len(plain) >= 4 else "****"
