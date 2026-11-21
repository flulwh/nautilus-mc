"""Outbound notifications (generic JSON webhook + Discord embed).

Design notes
------------
* Webhooks live in ``settings_kv`` instead of a dedicated table: they are a
  handful of records, edited as a whole, and never queried by field. That keeps
  the schema (and the Alembic chain) untouched.
* ``emit`` is synchronous on purpose. Call sites are often in sync code paths
  (process exit handlers, thread-pool callbacks); it schedules a task and
  returns immediately, so a slow or dead webhook can never delay a server
  stop or a backup.
* Delivery failures are logged and dropped. Retrying forever would turn a
  webhook outage into an unbounded queue of pending tasks.
* Discord is detected from the URL and gets an embed; everything else gets a
  flat JSON body that any automation (n8n, Alertmanager, a Lambda) can read.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select

from .db import SessionFactory
from .models import Server, SettingKV

log = logging.getLogger("nautilus.notify")

SETTINGS_KEY = "notify.webhooks"

#: Events the UI offers. ``*`` subscribes to everything.
EVENTS = [
    "server.started",
    "server.stopped",
    "server.crashed",
    "backup.created",
    "backup.restored",
    "task.failed",
    "install.failed",
    "player.join",
    "player.leave",
]

_COLORS = {
    "server.started": 0x6F9A5E,
    "server.stopped": 0x6B747D,
    "server.crashed": 0xC4553F,
    "backup.created": 0x5B7C99,
    "backup.restored": 0xB0A06A,
    "task.failed": 0xC4553F,
    "install.failed": 0xC4553F,
    "player.join": 0x6F9A5E,
    "player.leave": 0x6B747D,
}

_last_sent: dict[tuple[str, int, str], float] = {}
_MIN_GAP = 5.0  # seconds; crash loops must not produce a hundred messages


def _new_id() -> str:
    return "wh_" + uuid.uuid4().hex[:10]


def normalise(items: Any) -> list[dict]:
    """Coerce whatever is in the settings row into a safe, complete list."""
    out: list[dict] = []
    if not isinstance(items, list):
        return out
    for raw in items:
        if not isinstance(raw, dict):
            continue
        url = str(raw.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            continue
        events = raw.get("events")
        if not isinstance(events, list):
            events = ["*"]
        out.append(
            {
                "id": str(raw.get("id") or _new_id()),
                "name": str(raw.get("name", ""))[:60] or "webhook",
                "url": url[:500],
                "enabled": bool(raw.get("enabled", True)),
                "events": [str(e) for e in events if str(e) in {*EVENTS, "*"}],
                "format": raw.get("format") if raw.get("format") in {"auto", "discord", "json"} else "auto",
            }
        )
    return out


async def load() -> list[dict]:
    async with SessionFactory() as s:
        row = await s.get(SettingKV, SETTINGS_KEY)
    return normalise(row.value if row else [])


async def save(items: Any) -> list[dict]:
    clean = normalise(items)
    async with SessionFactory() as s:
        row = await s.get(SettingKV, SETTINGS_KEY)
        if row is None:
            s.add(SettingKV(key=SETTINGS_KEY, value=clean))
        else:
            row.value = clean
            s.add(row)
        await s.commit()
    return clean


def _wants(hook: dict, event: str) -> bool:
    if not hook.get("enabled", True):
        return False
    events = hook.get("events") or ["*"]
    return "*" in events or event in events


def _is_discord(url: str, fmt: str) -> bool:
    if fmt == "discord":
        return True
    if fmt == "json":
        return False
    return "discord.com/api/webhooks" in url or "discordapp.com/api/webhooks" in url


def _payload(hook: dict, event: str, server: dict | None, detail: dict, text: str) -> dict:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if _is_discord(hook["url"], hook.get("format", "auto")):
        fields = [{"name": k, "value": str(v)[:1000], "inline": True} for k, v in detail.items() if v not in (None, "")]
        embed: dict[str, Any] = {
            "title": text,
            "color": _COLORS.get(event, 0x6B747D),
            "timestamp": stamp,
            "footer": {"text": f"nautilus · {event}"},
        }
        if server:
            embed["description"] = f"**{server['name']}** · #{server['id']}"
        if fields:
            embed["fields"] = fields[:8]
        return {"username": "Nautilus", "embeds": [embed]}
    return {
        "panel": "nautilus",
        "event": event,
        "text": text,
        "timestamp": stamp,
        "server": server,
        "detail": detail,
    }


async def send(hook: dict, event: str, server: dict | None, detail: dict) -> bool:
    text = _text(event, server, detail)
    body = _payload(hook, event, server, detail, text)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0)) as c:
            r = await c.post(hook["url"], json=body, headers={"User-Agent": "nautilus-mc/0.1"})
        if r.status_code >= 400:
            log.warning("webhook %s returned %s for %s", hook["name"], r.status_code, event)
            return False
        return True
    except Exception as exc:  # noqa: BLE001 - notifications must never raise
        log.warning("webhook %s failed for %s: %s", hook["name"], event, exc)
        return False


def _text(event: str, server: dict | None, detail: dict) -> str:
    name = f"{server['name']} " if server else ""
    label = {
        "server.started": "服务器已启动",
        "server.stopped": "服务器已停止",
        "server.crashed": "服务器异常退出",
        "backup.created": "备份已完成",
        "backup.restored": "备份已恢复",
        "task.failed": "定时任务失败",
        "install.failed": "服务端安装失败",
        "player.join": "玩家加入",
        "player.leave": "玩家离开",
    }.get(event, event)
    extra = ""
    if detail.get("player"):
        extra = f"：{detail['player']}"
    elif detail.get("error"):
        extra = f"：{detail['error']}"
    elif detail.get("name"):
        extra = f"：{detail['name']}"
    return f"{name}{label}{extra}"


async def _dispatch(event: str, server_id: int | None, detail: dict) -> None:
    try:
        hooks = [h for h in await load() if _wants(h, event)]
    except Exception as exc:  # noqa: BLE001 - DB hiccup must not break the caller
        log.warning("could not load webhooks: %s", exc)
        return
    if not hooks:
        return
    server: dict | None = None
    if server_id is not None:
        try:
            async with SessionFactory() as s:
                row = (await s.execute(select(Server).where(Server.id == server_id))).scalar_one_or_none()
            if row is not None:
                server = {"id": row.id, "name": row.name, "kind": row.kind, "version": row.version}
        except Exception:  # noqa: BLE001
            server = {"id": server_id, "name": f"#{server_id}", "kind": "", "version": ""}
    await asyncio.gather(*(send(h, event, server, detail) for h in hooks), return_exceptions=True)


def emit(event: str, *, server_id: int | None = None, detail: dict | None = None) -> None:
    """Schedule delivery without blocking the caller.

    ``detail`` is coerced to strings because a Discord embed field rejects
    nested objects and an oversized value kills the whole message.
    """
    key = (event, server_id or 0, "")
    now = time.monotonic()
    if now - _last_sent.get(key, 0.0) < _MIN_GAP:
        return
    _last_sent[key] = now
    clean = {k: (str(v)[:500] if v is not None else "") for k, v in (detail or {}).items()}
    try:
        asyncio.get_running_loop().create_task(_dispatch(event, server_id, clean))
    except RuntimeError:  # no running loop (tests, CLI)
        log.debug("no event loop; dropping %s", event)


async def test(hook: dict) -> bool:
    return await send(hook, "server.started", None, {"note": "这是一条测试通知"})
