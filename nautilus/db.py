"""Async database bootstrap.

Schema evolution is handled by **Alembic** (``migrations/``), executed
programmatically on startup so that a fresh checkout works with a single
command. SQLite runs in WAL mode with a busy timeout, which keeps concurrent
readers (metrics, log tail) from blocking the writer.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .models import Base, Policy, Server, User
from .settings import settings

log = logging.getLogger("nautilus.db")
ROOT = Path(__file__).resolve().parent.parent

engine = create_async_engine(settings.db_url, echo=False, future=True, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

#: Used by Alembic, which needs a synchronous driver.
SYNC_DB_URL = f"sqlite:///{settings.db_path.as_posix()}"


@event.listens_for(engine.sync_engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - driver level
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


DEFAULT_POLICIES: list[tuple[str, str, str, str]] = [
    # p, subject, object, action(regex)
    ("p", "admin", "*", ".*"),
    ("p", "operator", "server", "read|control|console|files|config|plugins|create|install|backup|players|tasks"),
    ("p", "operator", "audit", "read"),
    ("p", "moderator", "server", "read|console"),
    ("p", "viewer", "server", "read"),
    # g, child, parent -> role inheritance
    ("g", "moderator", "viewer", ""),
    ("g", "operator", "moderator", ""),
    ("g", "admin", "operator", ""),
]


def run_migrations() -> None:
    """Upgrade the schema to ``head`` using Alembic."""
    from alembic import command
    from alembic.config import Config

    cfg_path = ROOT / "alembic.ini"
    if not cfg_path.exists():  # pragma: no cover - packaged without migrations
        log.warning("alembic.ini not found, falling back to create_all")
        import asyncio

        async def _create() -> None:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        asyncio.run(_create())
        return

    cfg = Config(str(cfg_path))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", SYNC_DB_URL)
    command.upgrade(cfg, "head")


async def init_db() -> None:
    run_migrations()
    async with SessionFactory() as s:
        if not (await s.execute(select(Policy.id).limit(1))).first():
            for ptype, v0, v1, v2 in DEFAULT_POLICIES:
                s.add(Policy(ptype=ptype, v0=v0, v1=v1, v2=v2))
            await s.commit()
            log.info("seeded default RBAC policies")

        if not (await s.execute(select(User.id).limit(1))).first():
            await _bootstrap_admin(s)


async def _bootstrap_admin(s: AsyncSession) -> None:
    from fastapi_users.password import PasswordHelper

    pwd = settings.bootstrap_admin_password
    generated = False
    if not pwd:
        pwd = secrets.token_urlsafe(12)
        generated = True
    user = User(
        username=settings.bootstrap_admin_user,
        email=f"{settings.bootstrap_admin_user}@example.com",
        hashed_password=PasswordHelper().hash(pwd),
        role="admin",
        is_active=True,
        is_verified=True,
        is_superuser=True,
    )
    s.add(user)
    await s.commit()
    if generated:
        log.warning("=" * 64)
        log.warning("  Initial admin account: %s / %s", user.username, pwd)
        log.warning("  Change the password right after the first login.")
        log.warning("=" * 64)
    else:
        log.info("created bootstrap admin %r from NAUTILUS_BOOTSTRAP_ADMIN_PASSWORD", user.username)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Request-scoped session: commits when the handler completes normally.

    Routes therefore never need an explicit ``commit()``; a raised exception
    rolls the whole unit of work back.
    """
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def autostart_servers() -> list[Server]:
    async with SessionFactory() as s:
        rows = (await s.execute(select(Server).where(Server.auto_start.is_(True)))).scalars().all()
        return list(rows)
