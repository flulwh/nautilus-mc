"""World directory management and the gameplay settings that belong to it.

Minecraft stores everything about a world inside its own folder (``level.dat``
plus region files), and switches worlds purely through ``level-name`` in
``server.properties``. That makes world management a filesystem + one property
problem, not a database one - which is why no schema is involved here.

Safety rules, mirroring the backup module:

* every mutating call requires the server to be stopped (the JVM holds region
  files open; moving them under a running server corrupts the world),
* the active world can never be deleted, only switched away from,
* deletion *moves* the folder into ``.nautilus/world-trash/`` instead of
  ``rm -rf``. A mis-click on a 4 GB world is not recoverable otherwise.
"""

from __future__ import annotations

import logging
import re
import shutil
import time
from pathlib import Path
from typing import Any

import jproperties

from . import configs
from .backups import level_name

log = logging.getLogger("nautilus.worlds")

NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,32}$")
TRASH_DIR = Path(".nautilus") / "world-trash"

#: Keys exposed in the UI, with the type used by the form and the validator.
FIELDS: dict[str, dict[str, Any]] = {
    "level-name": {"type": "text", "label": "世界名称"},
    "level-seed": {"type": "text", "label": "种子"},
    "level-type": {
        "type": "enum",
        "label": "地形生成",
        "options": ["minecraft:normal", "minecraft:flat", "minecraft:large_biomes", "minecraft:amplified", "minecraft:single_biome_surface"],
    },
    "gamemode": {"type": "enum", "label": "默认游戏模式", "options": ["survival", "creative", "adventure", "spectator"]},
    "difficulty": {"type": "enum", "label": "难度", "options": ["peaceful", "easy", "normal", "hard"]},
    "max-players": {"type": "int", "label": "最大玩家数", "min": 1, "max": 1000},
    "view-distance": {"type": "int", "label": "视距（区块）", "min": 2, "max": 32},
    "simulation-distance": {"type": "int", "label": "模拟距离（区块）", "min": 2, "max": 32},
    "spawn-protection": {"type": "int", "label": "出生点保护半径", "min": 0, "max": 1000},
    "online-mode": {"type": "bool", "label": "正版验证"},
    "pvp": {"type": "bool", "label": "允许 PVP"},
    "hardcore": {"type": "bool", "label": "极限模式"},
    "allow-flight": {"type": "bool", "label": "允许飞行"},
    "white-list": {"type": "bool", "label": "启用白名单"},
    "enforce-whitelist": {"type": "bool", "label": "白名单强制踢出"},
    "motd": {"type": "text", "label": "服务器描述"},
}
TEXT_DEFAULTS = {
    "level-name": "world",
    "level-type": "minecraft:normal",
    "gamemode": "survival",
    "difficulty": "easy",
}
BOOL_DEFAULTS = {
    "online-mode": "true",
    "pvp": "true",
    "hardcore": "false",
    "allow-flight": "false",
    "white-list": "false",
    "enforce-whitelist": "false",
}


class WorldError(RuntimeError):
    pass


def _props_path(server) -> Path:
    return server.directory / "server.properties"


def read_props(server) -> dict[str, str]:
    path = _props_path(server)
    out: dict[str, str] = {}
    if not path.exists():
        return out
    props = jproperties.Properties()
    try:
        props.load(path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    except Exception:  # pragma: no cover - corrupt properties
        return out
    for key in props:
        value = props.get(key)
        out[str(key)] = str(value.data if value else "")
    return out


def _dir_size(path: Path) -> int:
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file():
                total += child.stat().st_size
        except OSError:
            continue
    return total


def list_worlds(server) -> list[dict]:
    """Every folder that looks like a world, plus a pending active world."""
    root = server.directory
    active = level_name(server)
    out: list[dict] = []
    if root.exists():
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if not (child / "level.dat").exists():
                continue
            stat = child.stat()
            out.append(
                {
                    "name": child.name,
                    "size": _dir_size(child),
                    "modified": stat.st_mtime,
                    "active": child.name == active,
                    "pending": False,
                    "has_nether": (child / "DIM-1").is_dir() or (root / f"{child.name}_nether").is_dir(),
                    "has_end": (child / "DIM1").is_dir() or (root / f"{child.name}_the_end").is_dir(),
                }
            )
    if not any(w["active"] for w in out):
        out.append(
            {
                "name": active,
                "size": 0,
                "modified": 0.0,
                "active": True,
                "pending": True,
                "has_nether": False,
                "has_end": False,
            }
        )
    out.sort(key=lambda w: (not w["active"], w["name"]))
    return out


def settings(server) -> dict:
    props = read_props(server)
    values: dict[str, Any] = {}
    for key, meta in FIELDS.items():
        raw = props.get(key)
        if raw is None and key in BOOL_DEFAULTS:
            raw = BOOL_DEFAULTS[key]
        if raw in (None, "") and key in TEXT_DEFAULTS:
            raw = TEXT_DEFAULTS[key]
        if meta["type"] == "bool":
            values[key] = str(raw or "false").lower() == "true"
        elif meta["type"] == "int":
            try:
                values[key] = int(raw) if raw not in (None, "") else 0
            except ValueError:
                values[key] = 0
        else:
            values[key] = raw or ""
    return {"values": values, "fields": FIELDS}


def _validate(values: dict[str, Any]) -> dict[str, str]:
    clean: dict[str, str] = {}
    for key, value in values.items():
        meta = FIELDS.get(key)
        if meta is None:
            continue
        if meta["type"] == "bool":
            clean[key] = "true" if bool(value) else "false"
        elif meta["type"] == "int":
            try:
                number = int(value)
            except (TypeError, ValueError) as exc:
                raise WorldError(f"{meta['label']} 必须是整数") from exc
            if not meta["min"] <= number <= meta["max"]:
                raise WorldError(f"{meta['label']} 需要在 {meta['min']}–{meta['max']} 之间")
            clean[key] = str(number)
        elif meta["type"] == "enum":
            text = str(value)
            if text not in meta["options"]:
                raise WorldError(f"{meta['label']} 取值无效：{text}")
            clean[key] = text
        else:
            text = str(value).replace("\n", " ").replace("\r", " ").strip()
            if key == "level-name":
                if not NAME_RE.match(text):
                    raise WorldError("世界名称只能用字母、数字、下划线、连字符（1–32 位）")
            elif key == "motd":
                text = text[:120]
            elif len(text) > 200:
                raise WorldError(f"{meta['label']} 过长")
            clean[key] = text
    return clean


def update_settings(server, values: dict[str, Any]) -> dict:
    clean = _validate(values)
    if not clean:
        raise WorldError("没有可更新的字段")
    path = _props_path(server)
    if path.exists():
        configs.backup_file(path, server.directory)
    configs.set_properties(path, clean)
    return settings(server)


def create(server, name: str, seed: str = "", level_type: str = "minecraft:normal") -> dict:
    if not NAME_RE.match(name or ""):
        raise WorldError("世界名称只能用字母、数字、下划线、连字符（1–32 位）")
    target = server.directory / name
    if target.exists():
        raise WorldError(f"世界 {name} 已存在")
    values = {"level-name": name, "level-type": level_type}
    if seed.strip():
        values["level-seed"] = seed.strip()
    return update_settings(server, values)


def activate(server, name: str) -> dict:
    if not NAME_RE.match(name or ""):
        raise WorldError("世界名称无效")
    if not (server.directory / name / "level.dat").exists():
        raise WorldError(f"世界 {name} 不存在（还没有生成过）")
    return update_settings(server, {"level-name": name})


def _trash(server, path: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder = server.directory / TRASH_DIR / f"{stamp}-{path.name}"
    folder.parent.mkdir(parents=True, exist_ok=True)
    index = 2
    while folder.exists():
        folder = folder.with_name(f"{stamp}-{path.name}-{index}")
        index += 1
    shutil.move(str(path), str(folder))
    return folder


def delete(server, name: str) -> dict:
    if not NAME_RE.match(name or ""):
        raise WorldError("世界名称无效")
    if name == level_name(server):
        raise WorldError("不能删除当前正在使用的世界，请先切换到其他世界")
    path = server.directory / name
    if not path.is_dir():
        raise WorldError(f"世界 {name} 不存在")
    moved = _trash(server, path)
    # Dimension folders sit next to the world, not inside it.
    for suffix in ("_nether", "_the_end"):
        sibling = server.directory / f"{name}{suffix}"
        if sibling.is_dir():
            _trash(server, sibling)
    log.info("moved world %s to %s", name, moved)
    return {"moved_to": str(moved.relative_to(server.directory))}
