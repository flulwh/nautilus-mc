"""World listing, switching, creation and deletion."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..audit import record
from ..deps import SessionDep, client_ip, require
from ..models import Server, User
from ..resources import worlds as svc
from ..servers.manager import manager

log = logging.getLogger("nautilus.api.worlds")

router = APIRouter(prefix="/servers/{server_id}/worlds", tags=["worlds"])
guard = require("server", "config")


class WorldCreate(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    seed: str = Field(default="", max_length=64)
    level_type: str = Field(default="minecraft:normal", max_length=64)


async def _server(session, server_id: int) -> Server:
    server = await session.get(Server, server_id)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "服务器不存在")
    return server


def _require_stopped(server_id: int) -> None:
    if manager.get(server_id).alive:
        raise HTTPException(status.HTTP_409_CONFLICT, "请先停止服务器：JVM 持有区块文件，热操作会损坏存档")


@router.get("", dependencies=[Depends(guard)])
async def list_worlds(server_id: int, session: SessionDep) -> dict:
    server = await _server(session, server_id)
    worlds = await asyncio.to_thread(svc.list_worlds, server)
    settings = await asyncio.to_thread(svc.settings, server)
    return {
        "worlds": worlds,
        "settings": settings["values"],
        "fields": settings["fields"],
        "running": manager.get(server_id).alive,
    }


@router.put("/settings")
async def update_settings(
    server_id: int,
    payload: dict,
    request: Request,
    session: SessionDep,
    actor: User = Depends(guard),
) -> dict:
    server = await _server(session, server_id)
    # Gameplay settings are written to server.properties and only re-read at
    # boot; applying them live would silently diverge from the file.
    _require_stopped(server_id)
    try:
        updated = await asyncio.to_thread(svc.update_settings, server, payload)
    except svc.WorldError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await record(
        session,
        action="world.settings",
        actor=actor,
        target=server.name,
        detail={"keys": sorted(payload)},
        ip=client_ip(request),
    )
    return updated


@router.post("", status_code=201)
async def create_world(
    server_id: int,
    payload: WorldCreate,
    request: Request,
    session: SessionDep,
    actor: User = Depends(guard),
) -> dict:
    server = await _server(session, server_id)
    _require_stopped(server_id)
    try:
        result = await asyncio.to_thread(svc.create, server, payload.name, payload.seed, payload.level_type)
    except svc.WorldError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await record(
        session,
        action="world.create",
        actor=actor,
        target=f"{server.name}:{payload.name}",
        detail={"level_type": payload.level_type, "seeded": bool(payload.seed)},
        ip=client_ip(request),
    )
    return result


@router.post("/{name}/activate")
async def activate_world(
    server_id: int,
    name: str,
    request: Request,
    session: SessionDep,
    actor: User = Depends(guard),
) -> dict:
    server = await _server(session, server_id)
    _require_stopped(server_id)
    try:
        result = await asyncio.to_thread(svc.activate, server, name)
    except svc.WorldError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await record(session, action="world.activate", actor=actor, target=f"{server.name}:{name}", ip=client_ip(request))
    return result


@router.delete("/{name}", status_code=204)
async def delete_world(
    server_id: int,
    name: str,
    request: Request,
    session: SessionDep,
    actor: User = Depends(guard),
) -> None:
    server = await _server(session, server_id)
    _require_stopped(server_id)
    try:
        result = await asyncio.to_thread(svc.delete, server, name)
    except svc.WorldError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await record(
        session,
        action="world.delete",
        actor=actor,
        target=f"{server.name}:{name}",
        detail=result,
        ip=client_ip(request),
    )
