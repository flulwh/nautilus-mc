"""Server lifecycle, console, metrics and provisioning endpoints."""

from __future__ import annotations

import asyncio
import logging
import secrets
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..audit import record
from ..db import SessionFactory
from ..deps import SessionDep, client_ip, require
from ..events import bus, topic
from ..models import Server, User
from ..resources import files as file_service
from ..resources.plugins import content_dir
from ..servers import provision
from ..servers.manager import ManagerError, free_port, manager, port_in_use
from ..settings import settings

log = logging.getLogger("nautilus.api.servers")
router = APIRouter(prefix="/servers", tags=["servers"])

_tasks: set[asyncio.Task] = set()


class ServerCreate(BaseModel):
    name: str = Field(min_length=2, max_length=48, pattern=r"^[A-Za-z0-9_.\- ]+$")
    kind: str = "paper"
    version: str = ""
    memory_mb: int = Field(default=2048, ge=512, le=65536)
    min_memory_mb: int = Field(default=512, ge=128, le=32768)
    port: int | None = None
    auto_start: bool = False
    auto_restart: bool = True
    eula_accepted: bool = False
    install: bool = True


class ServerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=48, pattern=r"^[A-Za-z0-9_.\- ]+$")
    version: str | None = None
    #: Relative to the server directory: a path with separators would let the
    #: supervisor execute a jar from anywhere on the host.
    jar: str | None = Field(default=None, min_length=1, max_length=120, pattern=r"^[^/\\:*?\"<>|]+$")
    java: str | None = None
    memory_mb: int | None = Field(default=None, ge=512, le=65536)
    min_memory_mb: int | None = Field(default=None, ge=128, le=32768)
    port: int | None = Field(default=None, ge=1024, le=65535)
    rcon_port: int | None = Field(default=None, ge=1024, le=65535)
    rcon_enabled: bool | None = None
    jvm_args: str | None = None
    server_args: str | None = None
    auto_start: bool | None = None
    auto_restart: bool | None = None
    eula_accepted: bool | None = None


class CommandIn(BaseModel):
    command: str = Field(min_length=1, max_length=1000)


async def _get(session, server_id: int) -> Server:
    server = await session.get(Server, server_id)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "服务器不存在")
    return server


def _payload(server: Server) -> dict:
    data = server.public()
    data["runtime"] = manager.snapshot(server.id)
    return data


def _pick_port(used: set[int]) -> int:
    """First OS-free port that no other server row has reserved.

    ``free_port`` only asks the kernel, so it happily returns a port another
    (stopped) server owns. Skipping those keeps server #2 from colliding with
    server #1 while both are down.
    """
    start = 25565
    for _ in range(200):
        candidate = free_port(start)
        if candidate not in used:
            return candidate
        start = candidate + 1
    raise HTTPException(status.HTTP_409_CONFLICT, "没有可用的游戏端口")


def _pick_rcon_port(port: int, reserved: set[int]) -> int:
    """RCON port that collides with neither a game port nor another RCON port.

    The old ``port + 10`` derivation silently produced a duplicate as soon as
    two servers were >= 10 ports apart (25565/25575 vs 25575/25585), and the
    second server then failed to start with "Failed to bind to port".
    """
    for candidate in range(port + 1, min(port + 200, 65535)):
        if candidate not in reserved and not port_in_use(candidate):
            return candidate
    raise HTTPException(status.HTTP_409_CONFLICT, "没有可用的 RCON 端口")


@router.get("", dependencies=[Depends(require("server", "read"))])
async def list_servers(session: SessionDep) -> list[dict]:
    rows = (await session.execute(select(Server).order_by(Server.id))).scalars().all()
    out = []
    for s in rows:
        item = _payload(s)
        try:
            # Walking a large world can take seconds; never do it on the event
            # loop, or the whole panel stalls while the list renders.
            item["disk_bytes"] = (
                await asyncio.to_thread(file_service.tree_size, s.directory) if s.directory.exists() else 0
            )
            item["jar_ready"] = (s.directory / s.jar).exists()
        except OSError:
            item["disk_bytes"] = 0
            item["jar_ready"] = False
        out.append(item)
    return out


@router.get("/kinds")
async def kinds() -> dict:
    return {
        "kinds": provision.KINDS,
        "manual": sorted(provision.MANUAL_KINDS),
    }


@router.get("/versions/{kind}", dependencies=[Depends(require("server", "create"))])
async def versions(kind: str) -> dict:
    if kind in provision.MANUAL_KINDS:
        return {"kind": kind, "versions": [], "manual": True}
    try:
        items = await provision.versions_for(kind)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"获取版本列表失败: {exc}") from exc
    seen: list[str] = []
    for v in items:
        if v not in seen:
            seen.append(v)
    return {"kind": kind, "versions": seen[:60]}


@router.post("", status_code=201)
async def create_server(
    payload: ServerCreate,
    request: Request,
    session: SessionDep,
    background: BackgroundTasks,
    actor: User = Depends(require("server", "create")),
) -> dict:
    if payload.kind not in provision.KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "不支持的服务端类型")
    exists = (await session.execute(select(Server).where(Server.name == payload.name))).first()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "服务器名称已存在")

    rows = (await session.execute(select(Server))).scalars().all()
    # A game port must not collide with another server's RCON port either.
    taken = {s.port for s in rows} | {s.rcon_port for s in rows}
    port = payload.port or _pick_port(taken)
    if port in taken:
        raise HTTPException(status.HTTP_409_CONFLICT, f"端口 {port} 已被其它服务器占用")

    server = Server(
        name=payload.name,
        kind=payload.kind,
        version=payload.version,
        memory_mb=payload.memory_mb,
        min_memory_mb=payload.min_memory_mb,
        port=port,
        rcon_port=_pick_rcon_port(port, taken | {port}),
        rcon_password=secrets.token_hex(8),
        auto_start=payload.auto_start,
        auto_restart=payload.auto_restart,
        eula_accepted=payload.eula_accepted,
    )
    session.add(server)
    await session.flush()
    server.directory.mkdir(parents=True, exist_ok=True)
    for sub in ("plugins", "mods", "logs"):
        (server.directory / sub).mkdir(exist_ok=True)
    if payload.eula_accepted:
        (server.directory / "eula.txt").write_text("# Accepted via the Nautilus panel\neula=true\n", encoding="utf-8")

    await record(
        session,
        action="server.create",
        actor=actor,
        target=server.name,
        detail={"kind": server.kind, "version": server.version, "port": port},
        ip=client_ip(request),
    )

    if payload.install and payload.kind not in provision.MANUAL_KINDS and payload.version:
        task = asyncio.create_task(_install_task(server.id, payload.kind, payload.version))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)

    return _payload(server)


@router.get("/{server_id}", dependencies=[Depends(require("server", "read"))])
async def get_server(server_id: int, session: SessionDep) -> dict:
    server = await _get(session, server_id)
    data = _payload(server)
    data["directory"] = str(server.directory)
    data["jar_ready"] = (server.directory / server.jar).exists()
    data["content_dir"] = str(content_dir(server))
    data["files"] = csv_names(server) if server.directory.exists() else []
    return data


def csv_names(server: Server) -> list[str]:
    try:
        return sorted(p.name for p in server.directory.iterdir() if p.is_file())[:40]
    except OSError:
        return []


@router.patch("/{server_id}")
async def update_server(
    server_id: int,
    payload: ServerUpdate,
    request: Request,
    session: SessionDep,
    actor: User = Depends(require("server", "config")),
) -> dict:
    server = await _get(session, server_id)
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"]:
        dup = (await session.execute(select(Server).where(Server.name == data["name"], Server.id != server_id))).first()
        if dup:
            raise HTTPException(status.HTTP_409_CONFLICT, "服务器名称已存在")

    rt = manager.get(server_id)
    new_port = data.get("port", server.port)
    new_rcon = data.get("rcon_port", server.rcon_port)
    if new_port == new_rcon:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "游戏端口与 RCON 端口不能相同")
    if rt.alive and (new_port != server.port or new_rcon != server.rcon_port):
        # The running JVM keeps listening on the old ports; accepting the edit
        # would make the panel disagree with reality until the next restart.
        raise HTTPException(status.HTTP_409_CONFLICT, "服务器运行中不能修改端口，请先停止服务器")
    if new_port != server.port or new_rcon != server.rcon_port:
        others = (await session.execute(select(Server).where(Server.id != server_id))).scalars().all()
        busy = {s.port for s in others} | {s.rcon_port for s in others}
        clash = busy & {new_port, new_rcon}
        if clash:
            raise HTTPException(status.HTTP_409_CONFLICT, f"端口 {', '.join(map(str, sorted(clash)))} 已被其它服务器占用")

    for key, value in data.items():
        if value is not None:
            setattr(server, key, value)
    session.add(server)
    await record(
        session,
        action="server.update",
        actor=actor,
        target=server.name,
        detail={k: str(v) for k, v in data.items()},
        ip=client_ip(request),
    )
    return _payload(server)


@router.delete("/{server_id}", status_code=204)
async def delete_server(
    server_id: int,
    request: Request,
    session: SessionDep,
    actor: User = Depends(require("server", "delete")),
    purge: bool = Query(False, description="同时删除服务器目录"),
) -> None:
    server = await _get(session, server_id)
    rt = manager.get(server_id)
    if rt.alive:
        await manager.stop(server, force=True)
    name = server.name
    directory = server.directory
    await session.delete(server)
    manager.forget(server_id)
    if purge and directory.exists():
        import shutil

        shutil.rmtree(directory, ignore_errors=True)
    await record(
        session,
        action="server.delete",
        actor=actor,
        target=name,
        detail={"purge": purge},
        ip=client_ip(request),
    )


# ------------------------------------------------------------------ control


async def _control(server_id: int, action: str, session, actor: User, request: Request) -> dict:
    server = await _get(session, server_id)
    try:
        if action == "start":
            await manager.start(server)
        elif action == "stop":
            await manager.stop(server)
        elif action == "restart":
            await manager.restart(server)
        elif action == "kill":
            await manager.stop(server, force=True)
        else:  # pragma: no cover
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "未知操作")
    except ManagerError as exc:
        await record(session, action=f"server.{action}", actor=actor, target=server.name, ok=False, detail={"error": str(exc)}, ip=client_ip(request))
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await record(session, action=f"server.{action}", actor=actor, target=server.name, ip=client_ip(request))
    return manager.snapshot(server_id)


@router.post("/{server_id}/start")
async def start_server(
    server_id: int,
    request: Request,
    session: SessionDep,
    actor: User = Depends(require("server", "control")),
) -> dict:
    return await _control(server_id, "start", session, actor, request)


@router.post("/{server_id}/stop")
async def stop_server(
    server_id: int,
    request: Request,
    session: SessionDep,
    actor: User = Depends(require("server", "control")),
) -> dict:
    return await _control(server_id, "stop", session, actor, request)


@router.post("/{server_id}/restart")
async def restart_server(
    server_id: int,
    request: Request,
    session: SessionDep,
    actor: User = Depends(require("server", "control")),
) -> dict:
    return await _control(server_id, "restart", session, actor, request)


@router.post("/{server_id}/kill")
async def kill_server(
    server_id: int,
    request: Request,
    session: SessionDep,
    actor: User = Depends(require("server", "control")),
) -> dict:
    return await _control(server_id, "kill", session, actor, request)


@router.post("/{server_id}/command")
async def send_command(
    server_id: int,
    payload: CommandIn,
    request: Request,
    session: SessionDep,
    actor: User = Depends(require("server", "console")),
) -> dict:
    server = await _get(session, server_id)
    try:
        output = await manager.send(server, payload.command)
    except ManagerError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await record(
        session,
        action="server.command",
        actor=actor,
        target=server.name,
        detail={"command": payload.command[:200]},
        ip=client_ip(request),
    )
    return {"output": output}


# ----------------------------------------------------------------- observability


@router.get("/{server_id}/logs", dependencies=[Depends(require("server", "read"))])
async def logs(server_id: int, session: SessionDep, limit: int = Query(500, ge=1, le=2000)) -> dict:
    await _get(session, server_id)  # 404 instead of an empty phantom runtime
    rt = manager.get(server_id)
    return {"lines": rt.console.snapshot(limit), "state": rt.state, "players": sorted(rt.console.players)}


@router.get("/{server_id}/metrics", dependencies=[Depends(require("server", "read"))])
async def metrics(server_id: int, session: SessionDep, limit: int = Query(240, ge=10, le=600)) -> dict:
    await _get(session, server_id)
    rt = manager.get(server_id)
    points = list(rt.metrics)[-limit:]
    return {"points": points, "interval": settings.metrics_interval, "state": rt.state}


# Player administration lives in ``api/players.py`` (whitelist / ops / bans).


# ---------------------------------------------------------------- provisioning


async def _install_task(server_id: int, kind: str, version: str) -> None:
    rt = manager.get(server_id)
    if rt.alive:
        bus.publish(topic(server_id, "progress"), {"task": "install", "pct": -1, "stage": "服务器正在运行，安装已取消"})
        return
    rt.install_pct = 0
    bus.publish(topic(server_id, "progress"), {"task": "install", "pct": 0, "stage": "解析下载地址"})
    try:
        artifact = await provision.resolve(kind, version)
        bus.publish(topic(server_id, "progress"), {"task": "install", "pct": 5, "stage": f"下载 {artifact.filename}"})

        async def on_progress(done: int, total: int) -> None:
            pct = int(done * 90 / total) + 5 if total else rt.install_pct
            rt.install_pct = min(95, pct)
            bus.publish(
                topic(server_id, "progress"),
                {"task": "install", "pct": rt.install_pct, "stage": f"{done // 1048576}MB / {total // 1048576 or '?'}MB"},
            )

        async with SessionFactory() as s:
            server = await s.get(Server, server_id)
            if server is None:
                return
            target = server.directory / server.jar
            await provision.download(
                artifact.url,
                target,
                expected_sha256=artifact.sha256,
                expected_sha1=artifact.sha1,
                progress=on_progress,
            )
            server.version = version
            server.kind = kind
            s.add(server)
            await s.commit()
        rt.install_pct = 100
        bus.publish(topic(server_id, "progress"), {"task": "install", "pct": 100, "stage": "完成"})
        bus.publish(topic(server_id, "log"), {"line": f"[nautilus] 服务端 {artifact.filename} 安装完成", "level": "info"})
    except Exception as exc:
        rt.install_pct = -1
        log.warning("install failed for server %s: %s", server_id, exc)
        bus.publish(topic(server_id, "progress"), {"task": "install", "pct": -1, "stage": str(exc)})


@router.post("/{server_id}/install")
async def install_server(
    server_id: int,
    payload: dict,
    request: Request,
    session: SessionDep,
    actor: User = Depends(require("server", "install")),
) -> dict:
    server = await _get(session, server_id)
    kind = payload.get("kind", server.kind)
    version = payload.get("version", server.version)
    if not version:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请指定版本")
    if kind not in provision.KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "不支持的服务端类型")
    if kind in provision.MANUAL_KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "该类型需要手动上传 jar")
    rt = manager.get(server_id)
    if rt.alive:
        raise HTTPException(status.HTTP_409_CONFLICT, "服务器运行中不能替换服务端 jar，请先停止服务器")
    if 0 <= rt.install_pct < 100:
        raise HTTPException(status.HTTP_409_CONFLICT, "已有安装任务在进行中")
    task = asyncio.create_task(_install_task(server_id, kind, version))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    await record(session, action="server.install", actor=actor, target=server.name, detail={"kind": kind, "version": version}, ip=client_ip(request))
    return {"started": True, "kind": kind, "version": version}


@router.post("/{server_id}/jar")
async def upload_jar(
    server_id: int,
    request: Request,
    session: SessionDep,
    actor: User = Depends(require("server", "install")),
) -> dict:
    """Manual jar upload for Forge/Bedrock/custom server types."""

    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "filename"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "缺少文件")
    server = await _get(session, server_id)
    if manager.get(server_id).alive:
        raise HTTPException(status.HTTP_409_CONFLICT, "服务器运行中不能替换 jar，请先停止服务器")
    name = Path(str(upload.filename)).name
    if not name.lower().endswith((".jar", ".zip")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "仅支持 .jar 或 .zip")
    size = await file_service.save_upload(server.directory / name, upload)
    server.jar = name
    session.add(server)
    await record(session, action="server.jar.upload", actor=actor, target=server.name, detail={"file": name, "size": size}, ip=client_ip(request))
    return {"filename": name, "size": size}


@router.get("/{server_id}/port-check")
async def port_check(server_id: int, session: SessionDep) -> dict:
    server = await _get(session, server_id)
    return {"port": server.port, "in_use": port_in_use(server.port), "rcon": port_in_use(server.rcon_port)}
