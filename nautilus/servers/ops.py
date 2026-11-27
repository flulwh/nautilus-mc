"""Curated server operations shared by HTTP routes and AI tools.

Every action is a thin, safe wrapper around console/RCON commands so the
panel UI and the agent go through exactly the same code path.
"""

from __future__ import annotations

import logging
from typing import Any

from ..models import Server
from ..resources import players as player_service
from .manager import ManagerError, manager

log = logging.getLogger("nautilus.servers.ops")


def sanitize_message(message: str) -> str:
    """One console command per call: newlines would smuggle in a second one."""
    return message.replace("\r", " ").replace("\n", " ").strip()


async def _send(server: Server, command: str) -> str:
    return await manager.send(server, command)


async def save(server: Server) -> str:
    return await _send(server, "save-all")


async def reload(server: Server) -> str:
    return await _send(server, "reload")


async def broadcast(server: Server, message: str) -> tuple[str, str]:
    clean = sanitize_message(message)
    if not clean:
        raise ValueError("公告内容不能为空")
    return clean, await _send(server, f"say {clean}")


async def set_maintenance(
    server: Server,
    enabled: bool,
    kick_outsiders: bool = True,
    reason: str = "服务器维护中，请稍后重新加入",
) -> dict[str, Any]:
    """Toggle a whitelist gate. ``on`` also saves and (optionally) ejects
    everyone who is neither whitelisted nor op; ``off`` just lifts the gate.

    Requires a running server: the whitelist command itself makes the JVM
    persist ``white-list`` in ``server.properties`` (the panel must not write
    that file while the process holds it — Windows rejects the rename).
    """
    if not manager.get(server.id).alive:
        raise ManagerError("服务器未运行")
    if not enabled:
        output = await _send(server, "whitelist off")
        return {"enabled": False, "kicked": [], "output": output}
    await _send(server, "save-all")
    await _send(server, "whitelist reload")
    output = await _send(server, "whitelist on")
    kicked: list[str] = []
    if kick_outsiders:
        lists = player_service.lists(server)
        allowed = {
            str(r.get("name", "")).lower()
            for r in lists["whitelist"] + lists["ops"]
            if r.get("name")
        }
        reason_clean = sanitize_message(reason) or "维护中"
        for name in sorted(manager.get(server.id).console.players):
            if name.lower() in allowed:
                continue
            try:
                await manager.send(server, f"kick {name} {reason_clean}")
            except ManagerError:
                continue
            kicked.append(name)
    return {"enabled": True, "kicked": kicked, "output": output}
