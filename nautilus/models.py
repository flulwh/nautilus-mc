"""SQLAlchemy models.

``User`` extends fastapi-users' SQLAlchemy user table (mixin) so the whole
authentication stack - hashing, login, cookie transport, user CRUD - comes from
the library instead of being re-implemented here.

Runtime state of a server (pid, cpu, log tail) is intentionally *not* persisted:
it is rebuilt on boot from the jar and the config files on disk.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi_users.db import SQLAlchemyBaseUserTable
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime | None) -> str | None:
    """Serialise a DB timestamp as an explicit UTC instant.

    SQLite has no timezone type, so values come back naive even though they were
    written as UTC. Emitting them without an offset makes browsers read them as
    *local* time - an 8-hour skew in CST. Attaching UTC restores the truth.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


class Base(DeclarativeBase):
    pass


class User(SQLAlchemyBaseUserTable[int], Base):
    """fastapi-users user with an extra display name and RBAC role."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(32), default="viewer", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def display(self) -> str:
        return self.username or self.email

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "role": self.role,
            "is_active": self.is_active,
            "is_superuser": self.is_superuser,
            "created_at": iso_utc(self.created_at),
            "last_login_at": iso_utc(self.last_login_at),
        }


class Server(Base):
    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    kind: Mapped[str] = mapped_column(String(24), default="paper")
    version: Mapped[str] = mapped_column(String(32), default="")
    jar: Mapped[str] = mapped_column(String(255), default="server.jar")
    java: Mapped[str] = mapped_column(String(255), default="")
    memory_mb: Mapped[int] = mapped_column(Integer, default=2048)
    min_memory_mb: Mapped[int] = mapped_column(Integer, default=512)
    port: Mapped[int] = mapped_column(Integer, default=25565)
    rcon_port: Mapped[int] = mapped_column(Integer, default=25575)
    rcon_password: Mapped[str] = mapped_column(String(64), default="")
    rcon_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    jvm_args: Mapped[str] = mapped_column(Text, default="")
    server_args: Mapped[str] = mapped_column(Text, default="--nogui")
    auto_start: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_restart: Mapped[bool] = mapped_column(Boolean, default=True)
    eula_accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    @property
    def directory(self):
        from .settings import settings

        return settings.servers_dir / str(self.id)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "version": self.version,
            "jar": self.jar,
            "java": self.java,
            "memory_mb": self.memory_mb,
            "min_memory_mb": self.min_memory_mb,
            "port": self.port,
            "rcon_port": self.rcon_port,
            "rcon_enabled": self.rcon_enabled,
            "jvm_args": self.jvm_args,
            "server_args": self.server_args,
            "auto_start": self.auto_start,
            "auto_restart": self.auto_restart,
            "eula_accepted": self.eula_accepted,
            "created_at": iso_utc(self.created_at),
        }


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    actor: Mapped[str] = mapped_column(String(64), default="system")
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str] = mapped_column(String(128), default="")
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ip: Mapped[str] = mapped_column(String(64), default="")
    ok: Mapped[bool] = mapped_column(Boolean, default=True)


class PluginRecord(Base):
    __tablename__ = "plugin_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(24), default="modrinth")
    project_id: Mapped[str] = mapped_column(String(64), default="")
    slug: Mapped[str] = mapped_column(String(120), default="")
    name: Mapped[str] = mapped_column(String(160), default="")
    version: Mapped[str] = mapped_column(String(64), default="")
    filename: Mapped[str] = mapped_column(String(255), default="")
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "project_id": self.project_id,
            "slug": self.slug,
            "name": self.name,
            "version": self.version,
            "filename": self.filename,
            "installed_at": iso_utc(self.installed_at),
        }


class ApiToken(Base):
    """Long-lived bearer token for automation (CLI, CI, monitoring).

    Only the SHA-256 of the token is stored: a leaked database must not hand
    the attacker working credentials, exactly like a password hash. The plain
    value is shown once, at creation time.
    """

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(60), default="")
    prefix: Mapped[str] = mapped_column(String(16), default="nlt")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "prefix": self.prefix,
            "created_at": iso_utc(self.created_at),
            "last_used_at": iso_utc(self.last_used_at),
            "expires_at": iso_utc(self.expires_at),
        }


class Policy(Base):
    """Casbin policy rows (``p`` and ``g`` types)."""
    __tablename__ = "policies"

    id: Mapped[int] = mapped_column(primary_key=True)
    ptype: Mapped[str] = mapped_column(String(8), default="p")
    v0: Mapped[str] = mapped_column(String(64), default="")
    v1: Mapped[str] = mapped_column(String(128), default="")
    v2: Mapped[str] = mapped_column(String(64), default="")

    def line(self) -> str:
        parts = [self.ptype, self.v0, self.v1]
        if self.v2:
            parts.append(self.v2)
        return ", ".join(parts)


class SettingKV(Base):
    __tablename__ = "settings_kv"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ScheduledTask(Base):
    """Cron-driven maintenance job bound to one server (restart/backup/command)."""

    __tablename__ = "scheduled_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(24), default="command")
    cron: Mapped[str] = mapped_column(String(64), default="0 4 * * *")
    payload: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str] = mapped_column(String(16), default="")
    last_result: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "server_id": self.server_id,
            "name": self.name,
            "kind": self.kind,
            "cron": self.cron,
            "payload": self.payload,
            "enabled": self.enabled,
            "last_run": iso_utc(self.last_run),
            "last_status": self.last_status,
            "last_result": self.last_result,
            "created_at": iso_utc(self.created_at),
        }
