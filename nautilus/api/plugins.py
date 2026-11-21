"""Plugin market endpoints (Modrinth + Hangar)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from ..audit import record
from ..deps import SessionDep, client_ip, require
from ..models import Server, User
from ..resources import plugins as svc

router = APIRouter(prefix="/servers/{server_id}/plugins", tags=["plugins"])


class InstallIn(BaseModel):
    project: str
    source: str = "modrinth"
    version_id: str = ""


class ToggleIn(BaseModel):
    filename: str
    enabled: bool


async def _server(session, server_id: int) -> Server:
    server = await session.get(Server, server_id)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "服务器不存在")
    return server


@router.get("", dependencies=[Depends(require("server", "plugins"))])
async def installed(server_id: int, session: SessionDep) -> dict:
    server = await _server(session, server_id)
    return {
        "installed": svc.installed(server),
        "folder": svc.content_dir(server).name,
        "kind": server.kind,
        "version": server.version,
    }


@router.get("/search", dependencies=[Depends(require("server", "plugins"))])
async def search(
    server_id: int,
    session: SessionDep,
    q: str = Query(..., min_length=1),
    source: str = Query("modrinth"),
    limit: int = Query(20, ge=1, le=40),
    offset: int = Query(0, ge=0),
) -> dict:
    server = await _server(session, server_id)
    try:
        results = await svc.search(q, server=server, source=source, limit=limit, offset=offset)
    except svc.PluginError as exc:
        # Incompatible source / rate limit: a caller-side condition, not an outage.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"插件源不可用: {exc}") from exc
    return {"results": results, "source": source, "query": q}


@router.get("/{source}/{project:path}/versions", dependencies=[Depends(require("server", "plugins"))])
async def versions(server_id: int, source: str, project: str, session: SessionDep) -> dict:
    server = await _server(session, server_id)
    if source not in {"modrinth", "hangar"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "未知插件源")
    try:
        return {"versions": await svc.versions(project, server=server, source=source)}
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.post("/install")
async def install(
    server_id: int,
    payload: InstallIn,
    request: Request,
    session: SessionDep,
    actor: User = Depends(require("server", "plugins")),
) -> dict:
    server = await _server(session, server_id)
    if payload.source not in {"modrinth", "hangar"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "未知插件源")
    try:
        result = await svc.install(server, payload.project, source=payload.source, version_id=payload.version_id)
    except svc.PluginError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"安装失败: {exc}") from exc
    await record(
        session,
        action="plugin.install",
        actor=actor,
        target=f"{server.name}:{result['filename']}",
        detail={"source": payload.source, "version": result["version"]},
        ip=client_ip(request),
    )
    return result


@router.post("/toggle")
async def toggle(
    server_id: int,
    payload: ToggleIn,
    request: Request,
    session: SessionDep,
    actor: User = Depends(require("server", "plugins")),
) -> dict:
    server = await _server(session, server_id)
    try:
        new_name = svc.toggle(server, payload.filename, payload.enabled)
    except (svc.PluginError, OSError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await record(
        session,
        action="plugin.toggle",
        actor=actor,
        target=f"{server.name}:{payload.filename}",
        detail={"enabled": payload.enabled},
        ip=client_ip(request),
    )
    return {"filename": new_name, "enabled": payload.enabled}


@router.delete("")
async def remove(
    server_id: int,
    request: Request,
    session: SessionDep,
    filename: str,
    actor: User = Depends(require("server", "plugins")),
) -> dict:
    server = await _server(session, server_id)
    try:
        svc.remove(server, filename)
    except (svc.PluginError, OSError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await record(session, action="plugin.delete", actor=actor, target=f"{server.name}:{filename}", ip=client_ip(request))
    return {"ok": True}


@router.get("/updates", dependencies=[Depends(require("server", "plugins"))])
async def updates(server_id: int, session: SessionDep) -> dict:
    server = await _server(session, server_id)
    try:
        return {"updates": await svc.check_updates(server)}
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"检查更新失败: {exc}") from exc
