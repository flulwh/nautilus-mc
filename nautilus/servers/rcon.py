"""RCON client wrapper.

Protocol implementation is delegated to ``async-mcrcon`` (MIT). We only add
what the panel needs: a hard timeout and normalised errors, because the
upstream client will happily block forever on a half-open socket.
"""

from __future__ import annotations

import asyncio
import logging

from async_mcrcon import ClientError, InvalidPassword, MinecraftClient

log = logging.getLogger("nautilus.rcon")

DEFAULT_TIMEOUT = 8.0


class RconError(RuntimeError):
    """Raised when the server cannot accept RCON commands."""


async def command(
    host: str,
    port: int,
    password: str,
    cmd: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    if not password:
        raise RconError("RCON 密码未配置")

    async def _run() -> str:
        async with MinecraftClient(host, port, password) as client:
            return await client.send(cmd)

    try:
        return await asyncio.wait_for(_run(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise RconError(f"RCON 超时（{timeout}s）") from exc
    except InvalidPassword as exc:
        raise RconError("RCON 密码错误，请检查 server.properties") from exc
    except (ClientError, OSError) as exc:
        raise RconError(f"RCON 连接失败: {exc}") from exc


async def probe(host: str, port: int, timeout: float = 2.0) -> bool:
    """Cheap TCP reachability check."""
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return True
