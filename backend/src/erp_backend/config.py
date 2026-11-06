"""Application settings, loaded from environment variables prefixed ``ERP_``.

Secrets are read from files (Docker secrets) via the ``*_file`` settings. The plain
``*_password`` settings exist only for the local test harness and are rejected in prod.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _read_secret(path: Path | None, inline: str | None, name: str) -> str:
    if path is not None:
        return path.read_text(encoding="utf-8").strip()
    if inline is not None:
        return inline
    raise RuntimeError(f"Secret '{name}' is not configured (set ERP_{name.upper()}_FILE).")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ERP_", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"

    db_host: str = "db"
    db_port: int = 5432
    db_name: str = "erp"
    db_app_user: str = "erp_app"
    db_app_password_file: Path | None = None
    db_app_password: str | None = None
    db_owner_user: str = "erp_owner"
    db_owner_password_file: Path | None = None
    db_owner_password: str | None = None
    db_pool_size: int = 5
    db_max_overflow: int = 3

    # Comma-separated list of exact origins allowed to make state-changing requests.
    allowed_origins: str = ""

    cookie_name: str = "__Host-erp_session"
    session_idle_minutes: int = 60
    session_absolute_hours: int = 12

    # Raw provider payload archive (append-only, content-addressed; Blueprint §4.1).
    raw_root: Path = Path("/data/raw")

    # Versioned XBRL → standard line-item mappings (Blueprint §4.4; repository ``mappings/``,
    # copied to /app/mappings in the image).
    mappings_root: Path = Path("/app/mappings")
    mapping_version: str = "v2"

    # SEC EDGAR: required contact User-Agent (owner-supplied; no default, D42).
    sec_user_agent: str | None = None

    login_max_attempts: int = 5
    login_window_seconds: int = 900

    @model_validator(mode="after")
    def _prod_requires_secret_files(self) -> Settings:
        if self.env == "prod" and (self.db_app_password is not None or self.db_owner_password):
            raise ValueError("Inline database passwords are not allowed in prod; use *_FILE.")
        if not self.cookie_name.startswith("__Host-"):
            raise ValueError("Session cookie name must use the __Host- prefix.")
        return self

    @property
    def allowed_origin_set(self) -> frozenset[str]:
        return frozenset(
            o.strip().rstrip("/") for o in self.allowed_origins.split(",") if o.strip()
        )

    def app_password(self) -> str:
        return _read_secret(self.db_app_password_file, self.db_app_password, "db_app_password")

    def owner_password(self) -> str:
        return _read_secret(
            self.db_owner_password_file, self.db_owner_password, "db_owner_password"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
