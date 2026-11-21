"""Audit log, meta and health endpoints."""

from __future__ import annotations

import asyncio
import platform
import subprocess
import sys
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from ..authz import ACTIONS, OBJECTS, ROLES, authz
from ..deps import SessionDep, require
from ..extensions import pm
from ..models import iso_utc, AuditLog, Server, User
from ..settings import settings

router = APIRouter(tags=["system"])
audit_router = APIRouter(prefix="/audit", tags=["audit"])


@audit_router.get("", dependencies=[Depends(require("audit", "read"))])
async def list_audit(
    session: SessionDep,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    action: str = Query(""),
    actor: str = Query(""),
) -> dict:
    stmt = select(AuditLog).order_by(AuditLog.id.desc())
    count_stmt = select(func.count()).select_from(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action.like(f"{action}%"))
        count_stmt = count_stmt.where(AuditLog.action.like(f"{action}%"))
    if actor:
        stmt = stmt.where(AuditLog.actor == actor)
        count_stmt = count_stmt.where(AuditLog.actor == actor)
    rows = (await session.execute(stmt.offset(offset).limit(limit))).scalars().all()
    # Count with the same filters, otherwise page 2 of a filtered view claims
    # the total of the unfiltered table.
    total = await session.scalar(count_stmt)
    return {
        "total": total,
        "entries": [
            {
                "id": r.id,
                "ts": iso_utc(r.ts),
                "actor": r.actor,
                "action": r.action,
                "target": r.target,
                "detail": r.detail,
                "ip": r.ip,
                "ok": r.ok,
            }
            for r in rows
        ],
    }


@router.get("/meta")
async def meta() -> dict:
    return {
        "name": "Nautilus",
        "version": "0.1.0",
        "tagline": "Minecraft 服务端管理面板",
        "roles": ROLES,
        "objects": OBJECTS,
        "actions": ACTIONS,
        "authz_ready": authz.ready,
        "runtime": settings.runtime,
        "extensions": sorted(pm.list_name_plugin() and [n for n, _ in pm.list_name_plugin()]),
        "limits": {
            "max_log_lines": settings.max_log_lines,
            "metrics_interval": settings.metrics_interval,
        },
        "server_kinds": ["paper", "purpur", "vanilla", "fabric", "forge", "bedrock", "custom"],
    }


@router.get("/system")
async def system_info(session: SessionDep, _=Depends(require("system", "read"))) -> dict:
    servers = (await session.execute(select(Server))).scalars().all()
    users = await session.scalar(select(func.count()).select_from(User))
    # ``java -version`` is a subprocess: keep it off the event loop.
    java = await asyncio.to_thread(java_version)
    return {
        "python": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.release()}",
        "java": java,
        "data_dir": str(settings.data_dir),
        "servers": len(servers),
        "users": users,
        "now": datetime.now(timezone.utc).isoformat(),
    }


def java_version() -> str:
    try:
        out = subprocess.run([settings.java, "-version"], capture_output=True, text=True, timeout=8)
        return (out.stderr or out.stdout).splitlines()[0].strip()
    except (OSError, subprocess.SubprocessError, IndexError):
        return "未检测到 Java"
