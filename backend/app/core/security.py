"""JWT（含 pwd_ver 校验四态）、bcrypt、AES-GCM（D28 key 加密）。"""

import base64
import uuid
from datetime import datetime, timedelta

import bcrypt
import jwt as pyjwt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import get_settings

ALG = "HS256"


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


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
