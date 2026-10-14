from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# 代码内置默认值（仅供本地开发；公网部署必须用 .env 覆盖）
DEFAULT_JWT_SECRET = "dev-only-secret-change-me-in-production-32"
DEFAULT_AES_KEY = "dev-only-aes-key-32-bytes-change-me!!"
DEFAULT_ADMIN_PASSWORD = "admin123456"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "WordType"
    version: str = "1.0.0"
    tz: str = "Asia/Shanghai"

    jwt_secret: str = DEFAULT_JWT_SECRET
    jwt_expire_days: int = 7
    aes_key: str = DEFAULT_AES_KEY  # 32 bytes for AES-256

    admin_username: str = "admin"
    admin_password: str = DEFAULT_ADMIN_PASSWORD
    # 邀请码注册（2026-09-21 开放测试起默认开启）：无有效邀请码不能自行注册，
    # 一码一用（注册即核销）。如需关闭在 .env 设 INVITE_REQUIRED=0。
    invite_required: bool = True
    public_deploy: bool = False

    database_url: str = f"sqlite+aiosqlite:///{(BASE_DIR / 'data' / 'wordtype.db').as_posix()}"
    data_dir: Path = BASE_DIR / "data"
    backup_dir: Path = BASE_DIR / "backup"
    files_dir: Path = BASE_DIR / "data" / "files"
    log_level: str = "INFO"

    scheduler_enabled: bool = True


def insecure_secret_names(s: Settings) -> list[str]:
    """返回仍在使用代码默认值（或强度不足）的密钥配置名。"""
    problems: list[str] = []
    if s.jwt_secret == DEFAULT_JWT_SECRET or len(s.jwt_secret) < 32:
        problems.append("JWT_SECRET")
    if s.aes_key == DEFAULT_AES_KEY or len(s.aes_key) < 32:
        problems.append("AES_KEY")
    if s.admin_password == DEFAULT_ADMIN_PASSWORD:
        problems.append("ADMIN_PASSWORD")
    return problems


def verify_secrets(s: Settings) -> None:
    """公网部署（PUBLIC_DEPLOY=1）时拒绝带着默认密钥启动。"""
    if not s.public_deploy:
        return
    problems = insecure_secret_names(s)
    if problems:
        raise RuntimeError(
            "检测到 PUBLIC_DEPLOY=1 但以下配置仍为默认值/强度不足，拒绝启动："
            + "、".join(problems)
            + "。请在 backend/.env 中设置强随机值（参考 backend/.env.example）。"
        )


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    for d in (s.data_dir, s.backup_dir, s.files_dir):
        d.mkdir(parents=True, exist_ok=True)
    return s
