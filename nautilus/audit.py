"""Immutable audit trail for every mutating action."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditLog, User

log = logging.getLogger("nautilus.audit")


async def record(
    session: AsyncSession | None,
    *,
    action: str,
    actor: User | str | None = None,
    target: str = "",
    detail: dict[str, Any] | None = None,
    ip: str = "",
    ok: bool = True,
) -> None:
    if isinstance(actor, User):
        actor_id, actor_name = actor.id, actor.username
    else:
        actor_id, actor_name = None, (actor or "system")

    row = AuditLog(
        actor_id=actor_id,
        actor=actor_name,
        action=action,
        target=target,
        detail=detail or {},
        ip=ip,
        ok=ok,
    )
    try:
        if session is not None:
            session.add(row)
            await session.flush()
        else:
            from .db import session_scope

            async with session_scope() as s:
                s.add(row)
    except Exception:  # pragma: no cover - auditing must never break the request
        log.exception("failed to persist audit entry %s", action)
