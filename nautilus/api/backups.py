"""Backup archives: create, list, restore, download, delete.

Creation and extraction are blocking filesystem work, so both run on a worker
thread (``asyncio.to_thread``); progress is streamed to the server's WebSocket
topic so the UI can show a percentage instead of a spinner of unknown length.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..audit import record
from ..deps import SessionDep, client_ip, require_any
from ..events import bus, topic
from ..models import Server, User
from ..resources import backups as svc
from ..servers.manager import manager
from ..settings import settings

log = logging.getLogger("nautilus.api.backups")

router = APIRouter(prefix="/servers/{server_id}/backups", tags=["backups"])

#: ``backup`` is grantable on its own; ``config`` is the historic fallback so
#: existing installations keep working without a policy migration.
guard = require_any(("server", "backup"), ("server", "config"))

_tasks: set[asyncio.Task] = set()
#: Server ids with an archive currently being written (kept out of ``rt.state``
#: so the supervisor's state machine stays untouched).
_in_progress: set[int] = set()


class BackupCreate(BaseModel):
    scope: str = Field(default="world", pattern="^(world|config|full)$")
    label: str = Field(default="", max_length=40)


async def _server(session, server_id: int) -> Server:
    server = await session.get(Server, server_id)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "服务器不存在")
    return server


@router.get("", dependencies=[Depends(guard)])
async def list_backups(server_id: int, session: SessionDep) -> dict:
    server = await _server(session, server_id)
    archives = [m.public() for m in svc.list_archives(server)]
    rt = manager.get(server_id)
    return {
        "archives": archives,
        "total_bytes": sum(a["bytes"] for a in archives),
        "keep": settings.backup_keep,
        "scopes": list(svc.SCOPES),
        "world_dirs": svc.world_dirs(server),
        "running": rt.alive,
        "in_progress": server_id in _in_progress,
    }


@router.post("", status_code=202)
async def create_backup(
    server_id: int,
    payload: BackupCreate,
    request: Request,
    session: SessionDep,
    actor: User = Depends(guard),
) -> dict:
    server = await _server(session, server_id)
    if server_id in _in_progress:
        raise HTTPException(status.HTTP_409_CONFLICT, "已有备份任务在进行中")
    _in_progress.add(server_id)
    bus.publish(topic(server_id, "backup"), {"stage": "准备", "pct": 0, "running": True})

    async def run() -> None:
        try:
            def progress(pct: int, stage: str) -> None:
                bus.publish(topic(server_id, "backup"), {"stage": stage, "pct": pct, "running": True})

            meta = await asyncio.to_thread(svc.create, server, payload.scope, payload.label, progress)
            bus.publish(
                topic(server_id, "backup"),
                {"stage": "完成", "pct": 100, "running": False, "archive": meta.name},
            )
            bus.publish(topic(server_id, "log"), {"line": f"[nautilus] 备份完成：{meta.name}（{meta.bytes // 1048576 or 1} MB）", "level": "info"})
            await record(
                None,
                action="backup.create",
                actor=actor,
                target=f"{server.name}:{meta.name}",
                detail={"scope": payload.scope, "entries": meta.entries, "bytes": meta.bytes},
                ip=client_ip(request),
            )
        except Exception as exc:
            log.warning("backup failed for server %s: %s", server_id, exc)
            bus.publish(topic(server_id, "backup"), {"stage": str(exc), "pct": -1, "running": False})
            await record(
                None,
                action="backup.create",
                actor=actor,
                target=server.name,
                ok=False,
                detail={"error": str(exc)},
                ip=client_ip(request),
            )
        finally:
            _in_progress.discard(server_id)

    task = asyncio.create_task(run(), name=f"backup-{server_id}")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"started": True, "scope": payload.scope, "label": payload.label}


@router.get("/{name}/download", dependencies=[Depends(guard)])
async def download_backup(server_id: int, name: str, session: SessionDep) -> FileResponse:
    server = await _server(session, server_id)
    try:
        path = svc.archive_path(server, name)
    except svc.BackupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return FileResponse(path, media_type="application/gzip", filename=path.name)


@router.post("/{name}/restore")
async def restore_backup(
    server_id: int,
    name: str,
    request: Request,
    session: SessionDep,
    actor: User = Depends(guard),
) -> dict:
    server = await _server(session, server_id)
    rt = manager.get(server_id)
    if rt.alive:
        raise HTTPException(status.HTTP_409_CONFLICT, "请先停止服务器再恢复备份")
    if server_id in _in_progress:
        # Restoring while an archive is still being written would delete and
        # rewrite the very files the archiver is reading.
        raise HTTPException(status.HTTP_409_CONFLICT, "备份任务进行中，请等待完成后再恢复")
    try:
        result = await asyncio.to_thread(svc.restore, server, name)
    except svc.BackupError as exc:
        await record(
            session,
            action="backup.restore",
            actor=actor,
            target=f"{server.name}:{name}",
            ok=False,
            detail={"error": str(exc)},
            ip=client_ip(request),
        )
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await record(
        session,
        action="backup.restore",
        actor=actor,
        target=f"{server.name}:{name}",
        detail={"replaced": result.get("replaced", [])},
        ip=client_ip(request),
    )
    return result


@router.delete("/{name}", status_code=204)
async def delete_backup(
    server_id: int,
    name: str,
    request: Request,
    session: SessionDep,
    actor: User = Depends(guard),
) -> None:
    server = await _server(session, server_id)
    try:
        svc.delete(server, name)
    except svc.BackupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    await record(session, action="backup.delete", actor=actor, target=f"{server.name}:{name}", ip=client_ip(request))
