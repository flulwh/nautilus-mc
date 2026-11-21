"""WebSocket endpoints: live console, state, metrics and install progress.

The JWT lives in the same HttpOnly cookie used by the REST API, so the handshake
is authenticated without putting a token in the query string (which would end
up in access logs).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi_users.db import SQLAlchemyUserDatabase

from .auth.backend import UserManager, get_jwt_strategy
from .authz import authz
from .db import SessionFactory
from .events import bus
from .models import Server, User
from .servers.manager import manager
from .settings import settings

log = logging.getLogger("nautilus.ws")

router = APIRouter()
HEARTBEAT = 20.0


@dataclass(frozen=True, slots=True)
class Principal:
    """Detached snapshot of the authenticated user.

    ``JWTStrategy.read_token`` returns a live ORM instance owned by the session
    that opened it. Anything stored on the WebSocket connection outlives that
    session, so we copy the three columns we actually need and let the session
    close instead of carrying a detached object around.
    """

    id: int
    email: str
    role: str


async def authenticate(ws: WebSocket) -> Principal | None:
    token = ws.cookies.get(settings.cookie_name)
    if not token:
        return None
    async with SessionFactory() as session:
        user_db = SQLAlchemyUserDatabase(session, User)
        user_manager = UserManager(user_db)
        try:
            user = await get_jwt_strategy().read_token(token, user_manager)
        except Exception:
            log.debug("websocket token rejected", exc_info=True)
            return None
        if user is None or not user.is_active:
            return None
        return Principal(id=user.id, email=user.email, role=user.role)


def _materialise(initial: list[dict] | Callable[[], list[dict]]) -> list[dict]:
    return initial() if callable(initial) else initial


async def _serve(
    ws: WebSocket,
    key: str,
    initial: list[dict] | Callable[[], list[dict]],
) -> None:
    async with bus.subscribe(key) as channel:
        await ws.accept()
        for item in _materialise(initial):
            await ws.send_json(item)

        async def pump() -> None:
            while True:
                item = await channel.queue.get()
                await ws.send_json(item)

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(HEARTBEAT)
                await ws.send_json({"t": "ping"})

        tasks = [asyncio.create_task(pump()), asyncio.create_task(heartbeat())]
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in pending:
                task.cancel()
            for task in done:
                with contextlib.suppress(WebSocketDisconnect, asyncio.CancelledError):
                    task.result()
        finally:
            for task in tasks:
                task.cancel()


@router.websocket("/ws/servers/{server_id}")
async def server_stream(ws: WebSocket, server_id: int) -> None:
    user = await authenticate(ws)
    if user is None or not authz.can(user.role, "server", "read"):
        await ws.accept()
        await ws.close(code=4401, reason="unauthorized")
        return

    async with SessionFactory() as session:
        server = await session.get(Server, server_id)
        if server is None:
            await ws.accept()
            await ws.close(code=4404, reason="server not found")
            return

    rt = manager.get(server_id)
    initial = [
        {"t": "state", **rt.snapshot()},
        {"t": "history", "lines": rt.console.snapshot(300)},
        # A batch backfill, distinct from the single-sample ``metrics`` frames
        # pushed live below; conflating the two corrupted the chart series.
        {"t": "metrics_history", "points": list(rt.metrics)[-120:]},
    ]
    try:
        await _serve(ws, f"server:{server_id}", initial)
    except WebSocketDisconnect:
        pass
    except RuntimeError as exc:  # pragma: no cover - client vanished mid-send
        log.debug("websocket closed: %s", exc)


@router.websocket("/ws/events")
async def global_stream(ws: WebSocket) -> None:
    user = await authenticate(ws)
    if user is None:
        await ws.accept()
        await ws.close(code=4401, reason="unauthorized")
        return
    try:
        await _serve(ws, "global", lambda: [{"t": "hello", "connections": bus.connections}])
    except WebSocketDisconnect:
        pass
    except RuntimeError as exc:  # pragma: no cover
        log.debug("websocket closed: %s", exc)
