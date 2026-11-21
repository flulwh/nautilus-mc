"""File browser endpoints (sandboxed to the server directory)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..audit import record
from ..deps import SessionDep, UserDep, client_ip, require
from ..models import Server, User
from ..resources import files as svc

router = APIRouter(prefix="/servers/{server_id}/files", tags=["files"])


class WriteIn(BaseModel):
    path: str
    content: str


class MkdirIn(BaseModel):
    path: str = Field(min_length=1)


class RenameIn(BaseModel):
    path: str
    name: str = Field(min_length=1, max_length=120)


async def _server(session, server_id: int) -> Server:
    server = await session.get(Server, server_id)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "服务器不存在")
    if not server.directory.exists():
        raise HTTPException(status.HTTP_409_CONFLICT, "服务器目录不存在")
    return server


@router.get("", dependencies=[Depends(require("server", "files"))])
async def list_dir(
    server_id: int,
    session: SessionDep,
    path: str = Query(""),
    hidden: bool = Query(False),
) -> dict:
    server = await _server(session, server_id)
    try:
        return svc.listing(server.directory, path, show_hidden=hidden)
    except svc.FileError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/content", dependencies=[Depends(require("server", "files"))])
async def read_file(server_id: int, session: SessionDep, path: str) -> dict:
    server = await _server(session, server_id)
    try:
        target = svc.resolve(server.directory, path)
        content = await svc.read_text(target)
    except (svc.FileError, OSError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"path": path, "content": content, "size": len(content.encode())}


@router.put("/content")
async def write_file(
    server_id: int,
    payload: WriteIn,
    request: Request,
    session: SessionDep,
    actor: User = Depends(require("server", "files")),
) -> dict:
    server = await _server(session, server_id)
    try:
        target = svc.resolve(server.directory, payload.path)
        await svc.write_text(target, payload.content)
    except (svc.FileError, OSError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await record(
        session,
        action="file.write",
        actor=actor,
        target=f"{server.name}:{payload.path}",
        detail={"bytes": len(payload.content)},
        ip=client_ip(request),
    )
    return {"ok": True, "size": target.stat().st_size}


@router.post("/mkdir", status_code=201)
async def make_dir(
    server_id: int,
    payload: MkdirIn,
    request: Request,
    session: SessionDep,
    actor: User = Depends(require("server", "files")),
) -> dict:
    server = await _server(session, server_id)
    try:
        svc.mkdir(server.directory, payload.path)
    except (svc.FileError, OSError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await record(session, action="file.mkdir", actor=actor, target=f"{server.name}:{payload.path}", ip=client_ip(request))
    return {"ok": True}


@router.post("/rename")
async def rename(
    server_id: int,
    payload: RenameIn,
    request: Request,
    session: SessionDep,
    actor: User = Depends(require("server", "files")),
) -> dict:
    server = await _server(session, server_id)
    try:
        new_path = svc.rename(server.directory, payload.path, payload.name)
    except (svc.FileError, OSError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await record(session, action="file.rename", actor=actor, target=f"{server.name}:{payload.path}", detail={"to": new_path}, ip=client_ip(request))
    return {"path": new_path}


@router.delete("")
async def delete(
    server_id: int,
    request: Request,
    session: SessionDep,
    path: str,
    recursive: bool = Query(False),
    actor: User = Depends(require("server", "files")),
) -> dict:
    server = await _server(session, server_id)
    try:
        svc.delete(server.directory, path, recursive=recursive)
    except (svc.FileError, OSError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await record(session, action="file.delete", actor=actor, target=f"{server.name}:{path}", detail={"recursive": recursive}, ip=client_ip(request))
    return {"ok": True}


@router.post("/upload", status_code=201)
async def upload(
    server_id: int,
    request: Request,
    session: SessionDep,
    path: str = Query(""),
    file: UploadFile = File(...),
    actor: User = Depends(require("server", "files")),
) -> dict:
    server = await _server(session, server_id)
    name = (file.filename or "upload.bin").split("/")[-1].split("\\")[-1]
    try:
        target = svc.resolve(server.directory, f"{path.strip('/')}/{name}" if path else name)
        size = await svc.save_upload(target, file)
    except (svc.FileError, OSError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await record(session, action="file.upload", actor=actor, target=f"{server.name}:{name}", detail={"size": size}, ip=client_ip(request))
    return {"filename": name, "size": size}


@router.get("/download", dependencies=[Depends(require("server", "files"))])
async def download(server_id: int, session: SessionDep, path: str) -> FileResponse:
    server = await _server(session, server_id)
    try:
        target = svc.resolve(server.directory, path)
    except svc.FileError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if not target.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文件不存在")
    return FileResponse(target, filename=target.name)
