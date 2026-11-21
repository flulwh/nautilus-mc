"""Audit log, meta and health endpoints."""

from __future__ import annotations

import asyncio
import logging
import platform
import subprocess
import sys
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import notify
from ..audit import record
from ..authz import ACTIONS, OBJECTS, ROLES, authz
from ..deps import SessionDep, client_ip, require, require_any
from ..events import bus
from ..extensions import pm
from ..models import iso_utc, AuditLog, Server, User
from ..servers import java as java_svc
from ..servers import provision
from ..settings import settings

log = logging.getLogger("nautilus.api.system")

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
        "server_kinds": provision.KINDS,
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


# ------------------------------------------------------------------ java runtime

_java_tasks: dict[int, asyncio.Task] = {}


@router.get("/system/java")
async def list_java(_=Depends(require_any(("system", "read"), ("server", "config")))) -> dict:
    runtimes = await java_svc.detect(refresh=True)
    return {
        "runtimes": [r.public() for r in runtimes],
        "majors": java_svc.MAJORS,
        "default": settings.java,
        "managed_dir": str(java_svc.managed_root()),
        "downloading": sorted(_java_tasks),
    }


@router.post("/system/java/{major}", status_code=202)
async def install_java(major: int, _=Depends(require("system", "manage"))) -> dict:
    if major not in java_svc.MAJORS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"仅支持 {', '.join(map(str, java_svc.MAJORS))}")
    running = _java_tasks.get(major)
    if running is not None and not running.done():
        raise HTTPException(status.HTTP_409_CONFLICT, f"Java {major} 正在下载")

    async def run() -> None:
        def progress(done: int, total: int, stage: str) -> None:
            bus.publish(
                "system:java",
                {
                    "major": major,
                    "pct": int(done * 100 / total) if total else 0,
                    "stage": stage,
                    "done": done,
                    "total": total,
                },
            )

        try:
            info = await java_svc.download(major, progress)
            bus.publish("system:java", {"major": major, "pct": 100, "stage": f"完成 {info.version}", "done": 1, "total": 1})
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            log.warning("java %s download failed: %s", major, exc)
            bus.publish("system:java", {"major": major, "pct": -1, "stage": str(exc)})
        finally:
            _java_tasks.pop(major, None)

    _java_tasks[major] = asyncio.create_task(run(), name=f"java-{major}")
    return {"started": True, "major": major}


@router.delete("/system/java", status_code=204)
async def delete_java(path: str = Query(min_length=4), _=Depends(require("system", "manage"))) -> None:
    try:
        await asyncio.to_thread(java_svc.remove, path)
    except java_svc.JavaError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


# -------------------------------------------------------------------- webhooks


class WebhookPayload(BaseModel):
    name: str = Field(default="webhook", max_length=60)
    url: str = Field(min_length=8, max_length=500)
    enabled: bool = True
    events: list[str] = Field(default_factory=lambda: ["*"])
    format: str = "auto"


class WebhookList(BaseModel):
    webhooks: list[WebhookPayload] = Field(default_factory=list)


@router.get("/system/webhooks")
async def get_webhooks(_=Depends(require("system", "read"))) -> dict:
    return {"webhooks": await notify.load(), "events": notify.EVENTS}


@router.put("/system/webhooks")
async def put_webhooks(
    payload: WebhookList,
    request: Request,
    session: SessionDep,
    actor: User = Depends(require("system", "manage")),
) -> dict:
    items = []
    for hook in payload.webhooks:
        if not hook.url.startswith(("http://", "https://")):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Webhook 必须是 http(s) 地址")
        items.append(hook.model_dump())
    saved = await notify.save(items)
    await record(
        session,
        action="system.webhooks",
        actor=actor,
        target=f"{len(saved)} 个渠道",
        detail={"events": sorted({e for h in saved for e in h["events"]})},
        ip=client_ip(request),
    )
    return {"webhooks": saved, "events": notify.EVENTS}


@router.post("/system/webhooks/test")
async def test_webhook(payload: WebhookPayload, _=Depends(require("system", "manage"))) -> dict:
    hook = {"name": payload.name, "url": payload.url, "format": payload.format, "events": payload.events, "enabled": True}
    ok = await notify.test(hook)
    if not ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "测试消息发送失败，请检查地址与网络")
    return {"ok": True}
