from __future__ import annotations

from functools import lru_cache
from ipaddress import ip_network
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def is_strong_operator_secret(value: str | None) -> bool:
    secret = value or ""
    return (
        len(secret) >= 32
        and len(set(secret)) >= 12
        and not any(
            marker in secret.lower()
            for marker in ("change", "replace", "example", "password", "secret")
        )
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LCC_", env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite:///./data/lcc.db"
    app_timezone: str = "UTC"
    bootstrap_token: str | None = None
    security_secret: str | None = None
    public_origin: str = "http://localhost:5173"
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
    allowed_hosts: list[str] = Field(default_factory=lambda: ["localhost", "testserver"])
    trusted_proxy_cidrs: list[str] = Field(default_factory=list)

    @field_validator("app_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @field_validator("trusted_proxy_cidrs")
    @classmethod
    def validate_proxy_cidrs(cls, values: list[str]) -> list[str]:
        for value in values:
            network = ip_network(value, strict=False)
            if network.prefixlen == 0:
                raise ValueError("Trusted proxy ranges cannot cover the entire address space.")
        return values

    @model_validator(mode="after")
    def validate_static_production_security(self) -> Settings:
        if self.environment != "production":
            return self
        origin = urlparse(self.public_origin)
        if (
            origin.scheme != "https"
            or not origin.hostname
            or origin.path not in {"", "/"}
            or origin.username is not None
            or origin.password is not None
            or origin.query
            or origin.fragment
            or origin.params
        ):
            raise ValueError("Production public_origin must be an HTTPS origin without a path.")
        if self.allowed_origins != [self.public_origin]:
            raise ValueError("Production allowed_origins must contain only public_origin.")
        if self.allowed_hosts != [origin.hostname]:
            raise ValueError("Production allowed_hosts must contain only the public hostname.")
        if not is_strong_operator_secret(self.security_secret):
            raise ValueError(
                "Production security_secret must be a non-placeholder value of 32+ characters."
            )
        if not self.trusted_proxy_cidrs:
            raise ValueError("Production trusted_proxy_cidrs must be configured explicitly.")
        if not self.database_url.startswith("sqlite:////"):
            raise ValueError("Production database_url must use an absolute SQLite path.")
        if not self.backup_directory.is_absolute():
            raise ValueError("Production backup_directory must be absolute.")
        raw_database_path = Path(self.database_url.removeprefix("sqlite:///"))
        if not raw_database_path.is_absolute():
            raise ValueError("Production database path must be absolute.")
        database_path = raw_database_path.resolve()
        backup_path = self.backup_directory.resolve()
        database_parent = database_path.parent
        if (
            backup_path == database_parent
            or backup_path in database_parent.parents
            or database_parent in backup_path.parents
        ):
            raise ValueError("Production database and backup paths must not overlap.")
        repository_root = Path(__file__).resolve().parents[2]
        if repository_root == database_path or repository_root in database_path.parents:
            raise ValueError("Production database must be outside the application tree.")
        if repository_root == backup_path or repository_root in backup_path.parents:
            raise ValueError("Production backups must be outside the application tree.")
        return self

    @property
    def secure_cookies(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


async def get_settings_dependency() -> Settings:
    return get_settings()
