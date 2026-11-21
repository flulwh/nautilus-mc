"""Configuration files: parse, edit and write back **without destroying comments**.

Format handling is delegated to round-trip parsers so that editing one value
does not reformat the whole file:

* ``.properties`` -> jproperties (key order + header preserved via ``store``)
* ``.yml/.yaml``  -> ruamel.yaml (round-trip loader keeps comments, anchors, quotes)
* ``.toml``       -> tomlkit (round-trip document model)
* ``.json``       -> stdlib json (Minecraft's own files are machine written)
* everything else -> plain text editor
"""

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

import jproperties
import tomlkit
from ruamel.yaml import YAML

log = logging.getLogger("nautilus.configs")

MAX_EDIT_BYTES = 2 * 1024 * 1024
BACKUP_KEEP = 20

PROPERTIES_FORMATS = {"properties", "conf", "cfg", "txt", "ini", "log", "sh", "bat"}
YAML_FORMATS = {"yml", "yaml"}
JSON_FORMATS = {"json", "mcmeta"}

#: Type hints for the most-edited server.properties keys (UI only).
PROPERTY_TYPES: dict[str, str] = {
    "server-port": "int",
    "max-players": "int",
    "view-distance": "int",
    "simulation-distance": "int",
    "online-mode": "bool",
    "pvp": "bool",
    "hardcore": "bool",
    "allow-flight": "bool",
    "allow-nether": "bool",
    "enable-command-block": "bool",
    "enable-rcon": "bool",
    "enable-status": "bool",
    "white-list": "bool",
    "enforce-whitelist": "bool",
    "spawn-protection": "int",
    "difficulty": ["peaceful", "easy", "normal", "hard"],
    "gamemode": ["survival", "creative", "adventure", "spectator"],
    "level-type": ["minecraft:normal", "minecraft:flat", "minecraft:large_biomes", "minecraft:amplified"],
    "motd": "str",
}


def detect_format(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    if suffix in YAML_FORMATS:
        return "yaml"
    if suffix == "toml":
        return "toml"
    if suffix in JSON_FORMATS:
        return "json"
    if suffix in PROPERTIES_FORMATS or suffix == "":
        return "properties" if suffix in {"properties", "conf", "cfg", "ini"} else "text"
    return "text"


# --------------------------------------------------------------------- backup


def _backup(path: Path, root: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target_dir = root / ".nautilus" / "backups"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{path.name}.{stamp}.bak"
    shutil.copy2(path, target)
    backups = sorted(target_dir.glob(f"{path.name}.*.bak"))
    for old in backups[:-BACKUP_KEEP]:
        old.unlink(missing_ok=True)
    return target


def _atomic_write(path: Path, payload: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def backup_file(path: Path, root: Path) -> Path | None:
    """Public alias used by the config restore endpoint."""
    return _backup(path, root)


# ----------------------------------------------------------------------- read


def read_text(path: Path, max_bytes: int = MAX_EDIT_BYTES) -> str:
    raw = path.read_bytes()
    if len(raw) > max_bytes:
        raise ValueError(f"文件过大（{len(raw)} bytes），请使用下载功能")
    if b"\x00" in raw[:4096]:
        raise ValueError("二进制文件无法在编辑器中打开")
    for enc in ("utf-8", "utf-8-sig", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _infer_type(key: str, value: str) -> str | list[str]:
    """Pick a form widget for a ``server.properties`` entry.

    The curated table only covers the keys worth a dedicated control; everything
    else (Minecraft adds keys every release) is inferred from the value so a
    ``true``/``false`` still renders as a switch instead of a text box.
    """
    known = PROPERTY_TYPES.get(key)
    if known:
        return known
    low = str(value).strip().lower()
    if low in {"true", "false"}:
        return "bool"
    if low and low.lstrip("-").isdigit() and len(low) <= 9:
        return "int"
    return "str"


def read_document(path: Path) -> dict[str, Any]:
    fmt = detect_format(path)
    text = read_text(path) if path.exists() else ""
    if fmt == "properties":
        props = jproperties.Properties()
        if text:
            props.load(text, encoding="utf-8")
        data = {k: v.data for k, v in props.items()}
        return {
            "format": fmt,
            "data": data,
            "types": {k: _infer_type(k, str(v)) for k, v in data.items()},
            "path": path.name,
        }
    if fmt == "yaml":
        data = YAML(typ="safe").load(text) if text.strip() else {}
        return {"format": fmt, "data": data or {}, "path": path.name}
    if fmt == "toml":
        data = tomlkit.parse(text) if text.strip() else {}
        return {"format": fmt, "data": _plain(data), "path": path.name}
    if fmt == "json":
        try:
            data = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 语法错误: {exc}") from exc
        return {"format": fmt, "data": data, "path": path.name}
    return {"format": "text", "data": {"content": text}, "path": path.name}


def _plain(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_plain(v) for v in obj]
    return obj.unwrap() if hasattr(obj, "unwrap") else obj


# ---------------------------------------------------------------------- write


def write_document(path: Path, new_data: dict[str, Any], root: Path) -> dict[str, Any]:
    """Merge ``new_data`` into the existing file, preserving formatting."""
    fmt = detect_format(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _backup(path, root)

    if fmt == "properties":
        props = jproperties.Properties()
        if path.exists():
            props.load(path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        for key, value in new_data.items():
            props[key] = "" if value is None else str(value)
        buf = io.BytesIO()
        props.store(buf, encoding="utf-8", strip_meta=True, timestamp=False)
        payload = buf.getvalue()
        _atomic_write(path, payload)

    elif fmt == "yaml":
        yaml = YAML()
        yaml.preserve_quotes = True
        yaml.width = 4096
        if path.exists() and path.read_text(encoding="utf-8").strip():
            doc = yaml.load(path.read_text(encoding="utf-8"))
        else:
            doc = {}
        merged = _deep_merge(doc if doc is not None else {}, new_data)
        buf = io.StringIO()
        yaml.dump(merged, buf)
        _atomic_write(path, buf.getvalue().encode("utf-8"))
        # read back to guarantee the written document still parses
        YAML(typ="safe").load(buf.getvalue())

    elif fmt == "toml":
        doc = tomlkit.parse(path.read_text(encoding="utf-8")) if path.exists() else tomlkit.document()
        for key, value in new_data.items():
            doc[key] = value
        _atomic_write(path, tomlkit.dumps(doc).encode("utf-8"))

    elif fmt == "json":
        _atomic_write(path, json.dumps(new_data, indent=2, ensure_ascii=False).encode("utf-8"))

    else:
        content = str(new_data.get("content", ""))
        _atomic_write(path, content.encode("utf-8"))

    return read_document(path)


def _deep_merge(base: Any, incoming: Any) -> Any:
    """Update scalars in place so ruamel keeps comments attached to keys."""
    if isinstance(base, dict) and isinstance(incoming, dict):
        for key, value in incoming.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                _deep_merge(base[key], value)
            else:
                base[key] = value
        return base
    return incoming


def validate_text(path: Path, text: str) -> None:
    """Raise ``ValueError`` when ``text`` is not valid for the file's format."""
    fmt = detect_format(path)
    if fmt == "yaml":
        YAML(typ="safe").load(text)
    elif fmt == "toml":
        tomlkit.parse(text)
    elif fmt == "json":
        json.loads(text)
    elif fmt == "properties":
        props = jproperties.Properties()
        props.load(text, encoding="utf-8")


def write_raw(path: Path, text: str, root: Path) -> None:
    """Validated raw write: backup -> validate -> atomic replace."""
    validate_text(path, text)
    _backup(path, root)
    _atomic_write(path, text.encode("utf-8"))


def write_text(path: Path, text: str, root: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _backup(path, root)
    _atomic_write(path, text.encode("utf-8"))


def set_properties(path: Path, values: dict[str, str]) -> None:
    """Low-level helper used by the supervisor (no backups, called on start)."""
    props = jproperties.Properties()
    if path.exists():
        props.load(path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    for key, value in values.items():
        props[key] = value
    buf = io.BytesIO()
    props.store(buf, encoding="utf-8", strip_meta=True, timestamp=False)
    _atomic_write(path, buf.getvalue())
