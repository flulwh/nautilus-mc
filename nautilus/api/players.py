"""Player administration: online list, whitelist, ops, bans.

File edits make the change permanent; RCON makes it effective immediately. Both
are attempted, and the response tells the UI whether the live server accepted
the command (``applied``) so a "已写入，重启后生效" hint can be shown when the
server is offline.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from ..audit import record
from ..deps import SessionDep, client_ip, require_any
from ..models import Server, User
from ..resources import players as svc
from ..servers import rcon as rcon_client
from ..servers.manager import manager

log = logging.getLogger("nautilus.api.players")

router = APIRouter(prefix="/servers/{server_id}/players", tags=["players"])
guard = require_any(("server", "players"), ("server", "control"))

#: Starlette renamed 422 in 0.49; keep working on either version.
UNPROCESSABLE = getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422)


class PlayerIn(BaseModel):
    name: str = Field(min_length=2, max_length=16)


class OpIn(PlayerIn):
    level: int = Field(default=4, ge=1, le=4)


class BanIn(PlayerIn):
    reason: str = Field(default="Banned by an operator", max_length=200)
    kick: bool = True


class KickIn(PlayerIn):
    reason: str = Field(default="Kicked by an operator", max_length=200)


class WhitelistModeIn(BaseModel):
    enabled: bool


async def _server(session, server_id: int) -> Server:
    server = await session.get(Server, server_id)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "服务器不存在")
    return server


async def _rcon(server: Server, command: str) -> bool:
    """Best-effort live apply. Never fails the request: the file is still written."""
    rt = manager.get(server.id)
    if not (rt.alive and server.rcon_enabled and server.rcon_password):
        return False
    try:
        await rcon_client.command("127.0.0.1", server.rcon_port, server.rcon_password, command, timeout=5)
        return True
    except rcon_client.RconError as exc:
        log.debug("rcon %r failed: %s", command, exc)
        return False


async def _reload_whitelist(server: Server) -> None:
    """Pull disk state into the server's memory *before* it writes the file back.

    ``whitelist add`` makes the server dump its in-memory list to
    ``whitelist.json``. If the panel had added entries to the file while the
    server was up, that dump would silently drop them. Reloading first makes
    memory match disk, so the dump preserves every entry.
    """
    await _rcon(server, "whitelist reload")


def _summary(server: Server) -> dict:
    data = svc.lists(server)
    rt = manager.get(server.id)
    data["online"] = sorted(rt.console.players)
    data["state"] = rt.state
    return data


@router.get("", dependencies=[Depends(guard)])
async def player_lists(server_id: int, session: SessionDep) -> dict:
    server = await _server(session, server_id)
    try:
        return _summary(server)
    except svc.PlayerError as exc:
        raise HTTPException(UNPROCESSABLE, str(exc)) from exc


@router.get("/lookup", dependencies=[Depends(guard)])
async def lookup(server_id: int, session: SessionDep, name: str = Query(..., max_length=16)) -> dict:
    server = await _server(session, server_id)
    try:
        return await svc.resolve(server, name)
    except svc.PlayerError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/whitelist", status_code=201)
async def whitelist_add(
    server_id: int,
    payload: PlayerIn,
    request: Request,
    session: SessionDep,
    actor: User = Depends(guard),
) -> dict:
    server = await _server(session, server_id)
    try:
        profile = await svc.resolve(server, payload.name)
    except svc.PlayerError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    # RCON first: the server rewrites the JSON from memory, so the file has to
    # be the last writer or its copy would win.
    await _reload_whitelist(server)
    applied = await _rcon(server, f"whitelist add {profile['name']}")
    try:
        entry = svc.whitelist_add(server, profile)
    except svc.PlayerError as exc:
        raise HTTPException(UNPROCESSABLE, str(exc)) from exc
    await record(
        session,
        action="player.whitelist.add",
        actor=actor,
        target=f"{server.name}:{profile['name']}",
        detail={"uuid": entry["uuid"], "source": profile["source"], "applied": applied},
        ip=client_ip(request),
    )
    return {"entry": entry, "source": profile["source"], "applied": applied, **_summary(server)}


@router.delete("/whitelist/{name}")
async def whitelist_remove(
    server_id: int,
    name: str,
    request: Request,
    session: SessionDep,
    actor: User = Depends(guard),
) -> dict:
    server = await _server(session, server_id)
    await _reload_whitelist(server)
    applied = await _rcon(server, f"whitelist remove {name}")
    try:
        removed = svc.whitelist_remove(server, name)
    except svc.PlayerError as exc:
        raise HTTPException(UNPROCESSABLE, str(exc)) from exc
    await record(session, action="player.whitelist.remove", actor=actor, target=f"{server.name}:{name}", detail={"removed": removed}, ip=client_ip(request))
    return {"removed": removed, "applied": applied, **_summary(server)}


@router.post("/ops", status_code=201)
async def op_add(
    server_id: int,
    payload: OpIn,
    request: Request,
    session: SessionDep,
    actor: User = Depends(guard),
) -> dict:
    server = await _server(session, server_id)
    try:
        profile = await svc.resolve(server, payload.name)
    except svc.PlayerError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    applied = await _rcon(server, f"op {profile['name']}")
    try:
        entry = svc.op_add(server, profile, payload.level)
    except svc.PlayerError as exc:
        raise HTTPException(UNPROCESSABLE, str(exc)) from exc
    await record(session, action="player.op", actor=actor, target=f"{server.name}:{profile['name']}", detail={"level": payload.level, "applied": applied}, ip=client_ip(request))
    return {"entry": entry, "applied": applied, **_summary(server)}


@router.delete("/ops/{name}")
async def op_remove(
    server_id: int,
    name: str,
    request: Request,
    session: SessionDep,
    actor: User = Depends(guard),
) -> dict:
    server = await _server(session, server_id)
    applied = await _rcon(server, f"deop {name}")
    try:
        removed = svc.op_remove(server, name)
    except svc.PlayerError as exc:
        raise HTTPException(UNPROCESSABLE, str(exc)) from exc
    await record(session, action="player.deop", actor=actor, target=f"{server.name}:{name}", detail={"removed": removed}, ip=client_ip(request))
    return {"removed": removed, "applied": applied, **_summary(server)}


@router.post("/bans", status_code=201)
async def ban_add(
    server_id: int,
    payload: BanIn,
    request: Request,
    session: SessionDep,
    actor: User = Depends(guard),
) -> dict:
    server = await _server(session, server_id)
    try:
        profile = await svc.resolve(server, payload.name)
    except svc.PlayerError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    applied = False
    if payload.kick:
        applied = await _rcon(server, f"ban {profile['name']} {payload.reason}")
        if not applied:
            applied = await _rcon(server, f"kick {profile['name']} {payload.reason}")
    try:
        entry = svc.ban_add(server, profile, payload.reason)
    except svc.PlayerError as exc:
        raise HTTPException(UNPROCESSABLE, str(exc)) from exc
    await record(
        session,
        action="player.ban",
        actor=actor,
        target=f"{server.name}:{profile['name']}",
        detail={"reason": payload.reason, "applied": applied},
        ip=client_ip(request),
    )
    return {"entry": entry, "applied": applied, **_summary(server)}


@router.delete("/bans/{name}")
async def ban_remove(
    server_id: int,
    name: str,
    request: Request,
    session: SessionDep,
    actor: User = Depends(guard),
) -> dict:
    server = await _server(session, server_id)
    applied = await _rcon(server, f"pardon {name}")
    try:
        removed = svc.ban_remove(server, name)
    except svc.PlayerError as exc:
        raise HTTPException(UNPROCESSABLE, str(exc)) from exc
    await record(session, action="player.pardon", actor=actor, target=f"{server.name}:{name}", detail={"removed": removed}, ip=client_ip(request))
    return {"removed": removed, "applied": applied, **_summary(server)}


@router.post("/kick")
async def kick(
    server_id: int,
    payload: KickIn,
    request: Request,
    session: SessionDep,
    actor: User = Depends(guard),
) -> dict:
    server = await _server(session, server_id)
    applied = await _rcon(server, f"kick {payload.name} {payload.reason}")
    if not applied:
        raise HTTPException(status.HTTP_409_CONFLICT, "服务器未运行或 RCON 不可用，无法踢出玩家")
    await record(session, action="player.kick", actor=actor, target=f"{server.name}:{payload.name}", detail={"reason": payload.reason}, ip=client_ip(request))
    return {"applied": True}


@router.post("/whitelist-mode")
async def set_whitelist_mode(
    server_id: int,
    payload: WhitelistModeIn,
    request: Request,
    session: SessionDep,
    actor: User = Depends(guard),
) -> dict:
    server = await _server(session, server_id)
    applied = await _rcon(server, f"whitelist {'on' if payload.enabled else 'off'}")
    try:
        svc.set_whitelist_mode(server, payload.enabled)
    except OSError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"无法写入 server.properties：{exc}") from exc
    await record(
        session,
        action="player.whitelist.mode",
        actor=actor,
        target=server.name,
        detail={"enabled": payload.enabled, "applied": applied},
        ip=client_ip(request),
    )
    return {"enabled": payload.enabled, "applied": applied, **_summary(server)}
