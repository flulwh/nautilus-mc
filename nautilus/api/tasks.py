"""CRUD for scheduled maintenance tasks (cron driven)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..audit import record
from ..deps import SessionDep, client_ip, require_any
from ..models import ScheduledTask, Server, User
from ..servers import tasks as svc

log = logging.getLogger("nautilus.api.tasks")

router = APIRouter(prefix="/servers/{server_id}/tasks", tags=["tasks"])
guard = require_any(("server", "tasks"), ("server", "config"))


class TaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    kind: str = Field(default="command", pattern="^(restart|start|stop|backup|command)$")
    cron: str = Field(default="0 4 * * *", max_length=64)
    payload: str = Field(default="", max_length=1000)
    enabled: bool = True


class TaskUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    kind: str | None = Field(default=None, pattern="^(restart|start|stop|backup|command)$")
    cron: str | None = Field(default=None, max_length=64)
    payload: str | None = Field(default=None, max_length=1000)
    enabled: bool | None = None


async def _server(session, server_id: int) -> Server:
    server = await session.get(Server, server_id)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "服务器不存在")
    return server


def _check_cron(cron: str) -> None:
    if not (cron or "").strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cron 表达式不能为空")
    try:
        svc.make_trigger(cron)
    except svc.TaskError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


def _check_payload(kind: str, payload: str) -> None:
    """Same rules on create and update: a task that cannot run is not saved."""
    if kind == "command" and not (payload or "").strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "执行指令类任务需要填写指令内容")
    if kind == "backup" and (payload or "").strip() not in {"", "world", "config", "full"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "备份范围只能是 world / config / full")


@router.get("", dependencies=[Depends(guard)])
async def list_tasks(server_id: int, session: SessionDep) -> dict:
    await _server(session, server_id)
    rows = (
        await session.execute(select(ScheduledTask).where(ScheduledTask.server_id == server_id).order_by(ScheduledTask.id))
    ).scalars().all()
    return {
        "tasks": [{**row.public(), "next_run": svc.next_run(row.id)} for row in rows],
        "kinds": svc.KINDS,
        "timezone": str(svc.scheduler.timezone) if svc.scheduler.running else None,
    }


@router.post("", status_code=201)
async def create_task(
    server_id: int,
    payload: TaskCreate,
    request: Request,
    session: SessionDep,
    actor: User = Depends(guard),
) -> dict:
    server = await _server(session, server_id)
    _check_cron(payload.cron)
    _check_payload(payload.kind, payload.payload)

    row = ScheduledTask(
        server_id=server_id,
        name=payload.name.strip(),
        kind=payload.kind,
        cron=" ".join(payload.cron.split()),
        payload=payload.payload.strip(),
        enabled=payload.enabled,
    )
    session.add(row)
    await session.flush()
    await record(
        session,
        action="task.create",
        actor=actor,
        target=f"{server.name}:{row.name}",
        detail={"kind": row.kind, "cron": row.cron},
        ip=client_ip(request),
    )
    task_id = row.id
    # Commit before re-arming: the scheduler reads the row from its own session.
    await session.commit()
    await svc.refresh(task_id)
    return {**row.public(), "next_run": svc.next_run(task_id)}


@router.patch("/{task_id}")
async def update_task(
    server_id: int,
    task_id: int,
    payload: TaskUpdate,
    request: Request,
    session: SessionDep,
    actor: User = Depends(guard),
) -> dict:
    server = await _server(session, server_id)
    row = await session.get(ScheduledTask, task_id)
    if row is None or row.server_id != server_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    data = payload.model_dump(exclude_unset=True)
    if "cron" in data:
        _check_cron(data["cron"] or "")
        data["cron"] = " ".join((data["cron"] or "").split())
    kind = data.get("kind") or row.kind
    body = data["payload"] if "payload" in data else row.payload
    if "kind" in data or "payload" in data:
        _check_payload(kind, body or "")
    for key, value in data.items():
        if value is not None:
            setattr(row, key, value)
    session.add(row)
    await record(
        session,
        action="task.update",
        actor=actor,
        target=f"{server.name}:{row.name}",
        detail={k: str(v) for k, v in data.items()},
        ip=client_ip(request),
    )
    await session.commit()
    await svc.refresh(task_id)
    return {**row.public(), "next_run": svc.next_run(task_id)}


@router.delete("/{task_id}", status_code=204)
async def delete_task(
    server_id: int,
    task_id: int,
    request: Request,
    session: SessionDep,
    actor: User = Depends(guard),
) -> None:
    server = await _server(session, server_id)
    row = await session.get(ScheduledTask, task_id)
    if row is None or row.server_id != server_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    name = row.name
    await session.delete(row)
    await record(session, action="task.delete", actor=actor, target=f"{server.name}:{name}", ip=client_ip(request))
    await session.commit()
    await svc.refresh(task_id)


@router.post("/{task_id}/run")
async def run_now(
    server_id: int,
    task_id: int,
    request: Request,
    session: SessionDep,
    actor: User = Depends(guard),
) -> dict:
    server = await _server(session, server_id)
    row = await session.get(ScheduledTask, task_id)
    if row is None or row.server_id != server_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    await record(session, action="task.run", actor=actor, target=f"{server.name}:{row.name}", ip=client_ip(request))
    # Release the write transaction first: the runner updates the same row from
    # its own session and SQLite allows exactly one writer at a time.
    await session.commit()
    try:
        return await svc.run_task(task_id, source="manual")
    except svc.TaskError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
