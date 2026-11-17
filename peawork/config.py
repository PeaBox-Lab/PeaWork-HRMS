from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SECRET = "dev-only-change-me"
DEFAULT_ADMIN_PASSWORD = "ChangeMe123!"


class Settings(BaseSettings):
    """Typed application configuration (environment prefix ``PEAWORK_``)."""

    model_config = SettingsConfigDict(
        env_prefix="PEAWORK_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "PeaWork HRMS"
    version: str = "2.0.0"
    env: str = "dev"  # dev | test | production
    debug: bool = False
    base_url: str = "http://127.0.0.1:8090"

    # Platform
    secret_key: str = DEFAULT_SECRET
    database_url: str = "sqlite:///./data/peawork_v2.db"
    allowed_hosts: str = "*"
    behind_proxy: bool = False
    auto_create_schema: bool | None = None

    # Sessions and cookies
    session_cookie_name: str = "pw_session"
    csrf_cookie_name: str = "pw_csrf"
    session_ttl_hours: int = 12
    session_idle_minutes: int = 120
    cookie_secure: bool | None = None
    cookie_domain: str | None = None

    # Bootstrap
    organization_name: str = "PeaWork Demo Organisation"
    organization_country: str = "IN"
    organization_timezone: str = "Asia/Kolkata"
    organization_currency: str = "INR"
    bootstrap_admin_email: str = "admin@example.com"
    bootstrap_admin_name: str = "Platform Administrator"
    bootstrap_admin_password: str | None = None
    seed_demo_data: bool = False

    # Storage
    storage_dir: str = "./data/documents"
    max_upload_bytes: int = 10 * 1024 * 1024
    allowed_upload_types: str = (
        "application/pdf,image/png,image/jpeg,text/plain,text/csv,"
        "application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

    # Observability
    metrics_token: str | None = None
    log_level: str = "INFO"

    # Background jobs (R2+)
    redis_url: str | None = None

    @property
    def is_production(self) -> bool:
        return self.env.lower() in {"production", "prod"}

    @property
    def is_test(self) -> bool:
        return self.env.lower() in {"test", "testing"}

    @property
    def secure_cookies(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.is_production or self.base_url.startswith("https://")

    @property
    def should_auto_create_schema(self) -> bool:
        if self.auto_create_schema is not None:
            return self.auto_create_schema
        return not self.is_production

    @property
    def allowed_host_list(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]

    @property
    def allowed_content_types(self) -> set[str]:
        return {t.strip() for t in self.allowed_upload_types.split(",") if t.strip()}

    @property
    def storage_path(self) -> Path:
        return Path(self.storage_dir).expanduser().resolve()

    @model_validator(mode="after")
    def _guard_production(self) -> "Settings":
        if self.is_production:
            if self.secret_key == DEFAULT_SECRET or len(self.secret_key) < 32:
                raise ValueError("PEAWORK_SECRET_KEY must be a random value of at least 32 characters in production")
            if self.database_url.startswith("sqlite"):
                raise ValueError("PostgreSQL is required in production (set PEAWORK_DATABASE_URL)")
            if not self.bootstrap_admin_password:
                raise ValueError("PEAWORK_BOOTSTRAP_ADMIN_PASSWORD is required for first boot in production")
            if self.bootstrap_admin_password == DEFAULT_ADMIN_PASSWORD:
                raise ValueError("Refusing the default bootstrap password in production")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()


__all__ = ["Settings", "get_settings", "reset_settings_cache", "DEFAULT_ADMIN_PASSWORD"]
