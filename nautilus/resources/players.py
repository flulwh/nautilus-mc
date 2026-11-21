"""Whitelist, operators and bans.

Minecraft keeps these in plain JSON files next to the jar, and *also* keeps an
in-memory copy while the server runs. The panel therefore treats the files as
the source of truth (so changes survive a restart) and asks RCON to apply the
same change immediately when the server happens to be up.

UUIDs: online-mode servers need the Mojang-assigned id, which is looked up via
the public profile endpoint (cached, with a short timeout). Offline-mode
servers use the well-known ``OfflinePlayer:<name>`` MD5 derivation, which is
what the server itself would pick - so the entry matches instead of creating a
duplicate.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import jproperties
from cachetools import TTLCache

log = logging.getLogger("nautilus.players")

NAME_RE = re.compile(r"^[A-Za-z0-9_]{2,16}$")
PROFILE_TTL = 6 * 3600
_profiles: TTLCache = TTLCache(maxsize=2048, ttl=PROFILE_TTL)

#: Serialises read-modify-write cycles over whitelist/ops/bans. Two admins
#: clicking "add" at the same time used to lose one of the two entries: both
#: read the old JSON, both wrote their own version, last one won.
_MUTATE_LOCK = threading.RLock()

WHITELIST = "whitelist.json"
OPS = "ops.json"
BANS = "banned-players.json"
BAN_IPS = "banned-ips.json"


class PlayerError(RuntimeError):
    pass


# ------------------------------------------------------------------- helpers


def _read_json(path: Path, default: list[dict]) -> list[dict]:
    if not path.exists():
        return list(default)
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "[]")
    except (ValueError, OSError) as exc:
        raise PlayerError(f"{path.name} 解析失败：{exc}") from exc
    if not isinstance(data, list):
        raise PlayerError(f"{path.name} 结构异常，应为数组")
    return [row for row in data if isinstance(row, dict)]


def _write_json(path: Path, rows: list[dict]) -> None:
    """Atomic write: the server may read these files at any moment."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(rows, fh, indent=2, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _norm_uuid(value: str) -> str:
    raw = str(value or "").replace("-", "").strip()
    if len(raw) != 32:
        raise PlayerError("UUID 格式不正确")
    return str(uuid.UUID(raw))


def offline_uuid(name: str) -> str:
    """The UUID an offline-mode server assigns (MD5, version 3)."""
    digest = hashlib.md5(f"OfflinePlayer:{name}".encode("utf-8")).digest()
    return str(uuid.UUID(bytes=digest, version=3))


def online_mode(server) -> bool:
    path = server.directory / "server.properties"
    if not path.exists():
        return True
    props = jproperties.Properties()
    try:
        props.load(path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    except Exception:  # pragma: no cover - corrupt file
        return True
    value = props.get("online-mode")
    return str(value.data if value else "true").strip().lower() != "false"


def whitelist_enabled(server) -> bool:
    path = server.directory / "server.properties"
    if not path.exists():
        return False
    props = jproperties.Properties()
    try:
        props.load(path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    except Exception:  # pragma: no cover
        return False
    value = props.get("white-list")
    return str(value.data if value else "false").strip().lower() == "true"


def validate_name(name: str) -> str:
    name = (name or "").strip()
    if not NAME_RE.match(name):
        raise PlayerError("玩家名只能是 2-16 位字母、数字或下划线")
    return name


# ------------------------------------------------------------------ profiles


async def resolve(server, name: str) -> dict[str, str]:
    """Return ``{name, uuid, source}`` for a player name."""
    name = validate_name(name)
    offline = not online_mode(server)
    # Online and offline UUIDs for the same name differ, so the mode is part of
    # the key. Offline UUIDs are also case-sensitive (the server hashes the name
    # exactly as typed), which is why only the online lookup lower-cases.
    cache_key = f"offline:{name}" if offline else f"online:{name.lower()}"
    cached = _profiles.get(cache_key)
    if cached:
        return {**cached, "name": name}

    if offline:
        profile = {"uuid": offline_uuid(name), "source": "offline"}
        _profiles[cache_key] = profile
        return {**profile, "name": name}

    try:
        async with httpx.AsyncClient(timeout=5.0, headers={"User-Agent": "NautilusMC/0.1"}) as client:
            resp = await client.get(f"https://api.minecraftservices.com/minecraft/profile/lookup/name/{name}")
        if resp.status_code == 200:
            payload = resp.json()
            profile = {"uuid": _norm_uuid(payload["id"]), "source": "mojang"}
            _profiles[cache_key] = profile
            return {**profile, "name": payload.get("name", name)}
        if resp.status_code == 404:
            raise PlayerError(f"找不到玩家 {name}：该账号不存在，或服务器是离线模式")
        raise PlayerError(f"查询玩家失败（HTTP {resp.status_code}）")
    except httpx.HTTPError as exc:
        raise PlayerError(f"无法连接 Mojang 查询玩家：{exc}") from exc


# --------------------------------------------------------------------- files


def lists(server) -> dict[str, Any]:
    root = server.directory
    return {
        "whitelist": _read_json(root / WHITELIST, []),
        "ops": _read_json(root / OPS, []),
        "bans": _read_json(root / BANS, []),
        "whitelist_enabled": whitelist_enabled(server),
        "online_mode": online_mode(server),
    }


def whitelist_add(server, profile: dict[str, str]) -> dict[str, str]:
    path = server.directory / WHITELIST
    with _MUTATE_LOCK:
        rows = _read_json(path, [])
        if any(str(r.get("uuid", "")).lower() == profile["uuid"].lower() for r in rows):
            return profile
        rows.append({"uuid": profile["uuid"], "name": profile["name"]})
        _write_json(path, rows)
    return profile


def whitelist_remove(server, name: str) -> bool:
    path = server.directory / WHITELIST
    with _MUTATE_LOCK:
        rows = _read_json(path, [])
        kept = [r for r in rows if str(r.get("name", "")).lower() != name.lower()]
        if len(kept) == len(rows):
            return False
        _write_json(path, kept)
    return True


def op_add(server, profile: dict[str, str], level: int = 4) -> dict[str, Any]:
    path = server.directory / OPS
    entry = {
        "uuid": profile["uuid"],
        "name": profile["name"],
        "level": max(1, min(4, int(level))),
        "bypassesPlayerLimit": False,
    }
    with _MUTATE_LOCK:
        rows = _read_json(path, [])
        rows = [r for r in rows if str(r.get("uuid", "")).lower() != profile["uuid"].lower()]
        rows.append(entry)
        _write_json(path, rows)
    return entry


def op_remove(server, name: str) -> bool:
    path = server.directory / OPS
    with _MUTATE_LOCK:
        rows = _read_json(path, [])
        kept = [r for r in rows if str(r.get("name", "")).lower() != name.lower()]
        if len(kept) == len(rows):
            return False
        _write_json(path, kept)
    return True


def ban_add(server, profile: dict[str, str], reason: str = "Banned by an operator") -> dict[str, Any]:
    path = server.directory / BANS
    entry = {
        "uuid": profile["uuid"],
        "name": profile["name"],
        "created": time.strftime("%Y-%m-%d %H:%M:%S +0000", time.gmtime()),
        "source": "Nautilus",
        "expires": "forever",
        "reason": (reason or "Banned by an operator")[:200],
    }
    with _MUTATE_LOCK:
        rows = _read_json(path, [])
        rows = [r for r in rows if str(r.get("uuid", "")).lower() != profile["uuid"].lower()]
        rows.append(entry)
        _write_json(path, rows)
    return entry


def ban_remove(server, name: str) -> bool:
    path = server.directory / BANS
    with _MUTATE_LOCK:
        rows = _read_json(path, [])
        kept = [r for r in rows if str(r.get("name", "")).lower() != name.lower()]
        if len(kept) == len(rows):
            return False
        _write_json(path, kept)
    return True


def set_whitelist_mode(server, enabled: bool) -> None:
    """Persist ``white-list`` / ``enforce-whitelist`` in server.properties."""
    from .configs import set_properties

    set_properties(
        server.directory / "server.properties",
        {"white-list": "true" if enabled else "false", "enforce-whitelist": "true" if enabled else "false"},
    )
