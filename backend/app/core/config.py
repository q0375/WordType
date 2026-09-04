from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "WordType"
    version: str = "1.0.0"
    tz: str = "Asia/Shanghai"

    jwt_secret: str = "dev-only-secret-change-me-in-production-32"
    jwt_expire_days: int = 7
    aes_key: str = "dev-only-aes-key-32-bytes-change-me!!"  # 32 bytes for AES-256

    admin_username: str = "admin"
    admin_password: str = "admin123456"
    invite_required: bool = False
    public_deploy: bool = False

    database_url: str = f"sqlite+aiosqlite:///{(BASE_DIR / 'data' / 'wordtype.db').as_posix()}"
    data_dir: Path = BASE_DIR / "data"
    backup_dir: Path = BASE_DIR / "backup"
    files_dir: Path = BASE_DIR / "data" / "files"
    log_level: str = "INFO"

    scheduler_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    for d in (s.data_dir, s.backup_dir, s.files_dir):
        d.mkdir(parents=True, exist_ok=True)
    return s
