"""Structured config editing (properties / YAML / TOML / JSON).

Route order matters here: the ``/{path:path}/...`` sub-routes must be declared
before the catch-all ``/{path:path}`` reader, otherwise FastAPI's path
converter swallows them.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from ..audit import record
from ..deps import SessionDep, UserDep, client_ip, require
from ..models import Server, User
from ..resources import configs as svc

router = APIRouter(prefix="/servers/{server_id}/config", tags=["configs"])

#: Files every admin ends up editing; existing ones are listed first.
COMMON = [
    "server.properties",
    "bukkit.yml",
    "spigot.yml",
    "paper-global.yml",
    "paper-world-defaults.yml",
    "config/paper-global.yml",
    "config/paper-world-defaults.yml",
    "eula.txt",
    "ops.json",
    "whitelist.json",
    "banned-players.json",
    "banned-ips.json",
    "permissions.yml",
    "commands.yml",
    "help.yml",
]


class ConfigWrite(BaseModel):
    data: dict


class RawWrite(BaseModel):
    content: str


async def _server(session, server_id: int) -> Server:
    server = await session.get(Server, server_id)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "服务器不存在")
    return server


def _safe(server: Server, path: str) -> Path:
    from ..resources.files import FileError, resolve

    try:
        target = resolve(server.directory, path)
    except FileError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if not target.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "配置文件不存在")
    return target


@router.get("/files", dependencies=[Depends(require("server", "config"))])
async def list_configs(server_id: int, session: SessionDep) -> dict:
    server = await _server(session, server_id)
    present = [p for p in COMMON if (server.directory / p).exists()]
    others: list[str] = []
    for pattern in ("*.yml", "*.yaml", "*.toml", "*.properties", "config/*.yml"):
        for path in server.directory.glob(pattern):
            rel = path.relative_to(server.directory).as_posix()
            if rel not in present and rel not in others and path.is_file():
                others.append(rel)
    return {"files": present + sorted(others)[:60]}


# --- specific sub-routes first (see module docstring) ---------------------


@router.get("/{path:path}/backups", dependencies=[Depends(require("server", "config"))])
async def backups(server_id: int, path: str, session: SessionDep) -> dict:
    server = await _server(session, server_id)
    target = _safe(server, path)
    folder = server.directory / ".nautilus" / "backups"
    items = sorted(folder.glob(f"{target.name}.*.bak"), reverse=True) if folder.exists() else []
    return {"backups": [{"name": p.name, "size": p.stat().st_size, "mtime": p.stat().st_mtime} for p in items[:20]]}


@router.post("/{path:path}/restore")
async def restore(
    server_id: int,
    path: str,
    request: Request,
    session: SessionDep,
    backup: str = Query(..., description="备份文件名"),
    actor: User = Depends(require("server", "config")),
) -> dict:
    server = await _server(session, server_id)
    target = _safe(server, path)
    folder = server.directory / ".nautilus" / "backups"
    source = (folder / backup).resolve()
    if not source.is_relative_to(folder.resolve()) or not source.exists():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "备份不存在")
    svc.backup_file(target, server.directory)
    shutil.copy2(source, target)
    await record(
        session,
        action="config.restore",
        actor=actor,
        target=f"{server.name}:{path}",
        detail={"backup": backup},
        ip=client_ip(request),
    )
    return {"ok": True, "restored_from": backup}


@router.post("/{path:path}/accept-eula")
async def accept_eula(
    server_id: int,
    path: str,
    request: Request,
    session: SessionDep,
    actor: User = Depends(require("server", "config")),
) -> dict:
    server = await _server(session, server_id)
    (server.directory / "eula.txt").write_text("# Accepted via the Nautilus panel\neula=true\n", encoding="utf-8")
    server.eula_accepted = True
    session.add(server)
    await record(session, action="server.eula", actor=actor, target=server.name, ip=client_ip(request))
    return {"ok": True}


# --- catch-all reader / writer -------------------------------------------


@router.put("/{path:path}/raw")
async def write_config_raw(
    server_id: int,
    path: str,
    payload: RawWrite,
    request: Request,
    session: SessionDep,
    actor: User = Depends(require("server", "config")),
) -> dict:
    """Source-mode save: syntax is validated *before* the file is replaced."""
    server = await _server(session, server_id)
    target = _safe(server, path)
    try:
        svc.write_raw(target, payload.content, server.directory)
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"语法校验失败，未写入：{exc}") from exc
    await record(
        session,
        action="config.raw_write",
        actor=actor,
        target=f"{server.name}:{path}",
        detail={"bytes": len(payload.content)},
        ip=client_ip(request),
    )
    return {"ok": True, "bytes": len(payload.content)}


@router.get("/{path:path}", dependencies=[Depends(require("server", "config"))])
async def read_config(server_id: int, path: str, session: SessionDep) -> dict:
    server = await _server(session, server_id)
    target = _safe(server, path)
    try:
        return svc.read_document(target)
    except (ValueError, OSError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.put("/{path:path}")
async def write_config(
    server_id: int,
    path: str,
    payload: ConfigWrite,
    request: Request,
    session: SessionDep,
    actor: User = Depends(require("server", "config")),
) -> dict:
    server = await _server(session, server_id)
    target = _safe(server, path)
    try:
        result = svc.write_document(target, payload.data, server.directory)
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"写入失败（已回滚）: {exc}") from exc
    await record(
        session,
        action="config.write",
        actor=actor,
        target=f"{server.name}:{path}",
        detail={"keys": list(payload.data)[:20]},
        ip=client_ip(request),
    )
    return result
