from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LCC_", env_file=".env", extra="ignore")

    environment: str = "development"
    database_url: str = "sqlite:///./data/lcc.db"
    app_timezone: str = "UTC"
    bootstrap_token: str | None = None
    session_cookie_name: str = "lcc_session"
    session_idle_timeout_ms: int = 7 * 24 * 60 * 60 * 1000
    session_absolute_timeout_ms: int = 30 * 24 * 60 * 60 * 1000
    max_import_bytes: int = 10 * 1024 * 1024
    login_rate_limit_attempts: int = 5
    login_rate_limit_window_ms: int = 5 * 60 * 1000
    import_rate_limit_attempts: int = 10
    import_rate_limit_window_ms: int = 10 * 60 * 1000
    backup_directory: Path = Path("./backups")
    allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    @field_validator("app_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @property
    def secure_cookies(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


async def get_settings_dependency() -> Settings:
    return get_settings()
