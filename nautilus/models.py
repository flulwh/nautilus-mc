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
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,
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
            "created_at": self.created_at.isoformat() if self.created_at else None,
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
            "installed_at": self.installed_at.isoformat() if self.installed_at else None,
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
