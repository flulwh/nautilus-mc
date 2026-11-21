"""Runtime configuration.

Everything is overridable through ``NAUTILUS_*`` environment variables or a
``.env`` file next to the working directory. Defaults are deliberately
conservative: bind to loopback, no self-registration, tokens expire in 2h.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NAUTILUS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- network -------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8199
    root_path: str = ""
    #: Comma-separated PROXY protocol / X-Forwarded-For peers we trust.
    #: Set to "*" only when the panel sits behind a proxy you control.
    forwarded_allow_ips: str = "127.0.0.1"
    cors_origins: list[str] = Field(default_factory=list)

    # --- storage -------------------------------------------------------
    data_dir: Path = Path("data")

    # --- security ------------------------------------------------------
    secret_key: str = ""
    token_ttl_minutes: int = 120
    cookie_name: str = "nautilus_session"
    cookie_secure: bool = False
    allow_registration: bool = False
    login_max_attempts: int = 5
    login_lock_minutes: int = 15
    #: When set, the first user with this password is created on boot.
    bootstrap_admin_password: str = ""
    bootstrap_admin_user: str = "admin"

    # --- runtime -------------------------------------------------------
    java: str = "java"
    runtime: Literal["local", "docker"] = "local"
    max_log_lines: int = 2000
    metrics_interval: float = 3.0
    metrics_points: int = 600
    stop_grace_seconds: int = 30
    log_level: str = "info"
    serve_frontend: bool = True

    @field_validator("data_dir", mode="before")
    @classmethod
    def _expand(cls, v: object) -> object:
        if isinstance(v, str):
            return Path(v).expanduser()
        return v

    # --- derived paths -------------------------------------------------
    @property
    def db_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path.as_posix()}"

    @property
    def db_path(self) -> Path:
        return (self.data_dir / "nautilus.db").resolve()

    @property
    def servers_dir(self) -> Path:
        return (self.data_dir / "servers").resolve()

    @property
    def webui_dir(self) -> Path:
        return (Path(__file__).parent / "webui" / "dist").resolve()

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.servers_dir.mkdir(parents=True, exist_ok=True)

    def resolve_secret(self) -> str:
        """Use the configured key, otherwise generate and persist one."""
        if self.secret_key:
            return self.secret_key
        key_file = self.data_dir / ".secret"
        if key_file.exists():
            self.secret_key = key_file.read_text(encoding="utf-8").strip()
            return self.secret_key
        self.secret_key = secrets.token_urlsafe(48)
        key_file.write_text(self.secret_key, encoding="utf-8")
        try:  # best effort on POSIX
            key_file.chmod(0o600)
        except OSError:
            pass
        return self.secret_key


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    s.resolve_secret()
    return s


settings = get_settings()
