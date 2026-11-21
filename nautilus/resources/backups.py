"""Whole-server archives: create, list, restore, prune.

``tarfile`` from the standard library does the container work; the value added
here is *what* to include (world folders resolved from ``level-name``, configs,
everything except logs and the panel's own bookkeeping) and a restore path that
is safe against hostile archives and refuses to run while the JVM holds the
region files open.

Archives live in ``<server>/.nautilus/archives`` with a JSON sidecar holding the
metadata, so listing never has to open a multi-gigabyte tarball.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import jproperties

log = logging.getLogger("nautilus.backups")

ARCHIVE_DIRNAME = ".nautilus/archives"
PRERESTORE_DIRNAME = ".nautilus/pre-restore"

SCOPES = ("world", "config", "full")

#: Never archived: the archives themselves, console mirrors and caches.
ALWAYS_EXCLUDE = {".nautilus", "logs", "cache", "crash-reports"}

#: Files worth keeping when only the configuration is requested.
CONFIG_SUFFIXES = {".properties", ".yml", ".yaml", ".toml", ".json", ".conf", ".cfg", ".txt", ".ini"}

_SAFE_LABEL = re.compile(r"[^A-Za-z0-9_\-\u4e00-\u9fa5]+")

Progress = Callable[[int, str], None]


class BackupError(RuntimeError):
    pass


@dataclass
class ArchiveMeta:
    name: str
    scope: str
    label: str
    created_at: float
    bytes: int
    entries: int
    includes: list[str]

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "scope": self.scope,
            "label": self.label,
            "created_at": self.created_at,
            "bytes": self.bytes,
            "entries": self.entries,
            "includes": self.includes,
        }


# --------------------------------------------------------------------- paths


def archive_dir(server) -> Path:
    return server.directory / ARCHIVE_DIRNAME


def level_name(server) -> str:
    """Read ``level-name`` from server.properties (default ``world``)."""
    path = server.directory / "server.properties"
    if not path.exists():
        return "world"
    props = jproperties.Properties()
    try:
        props.load(path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    except Exception:  # pragma: no cover - corrupt props
        return "world"
    value = props.get("level-name")
    name = (value.data if value else "") or "world"
    # A level name is a directory name; refuse anything that escapes the root.
    return Path(str(name)).name or "world"


def world_dirs(server) -> list[str]:
    level = level_name(server)
    found: list[str] = []
    for child in sorted(server.directory.iterdir()) if server.directory.exists() else []:
        if not child.is_dir():
            continue
        if child.name == level or child.name.startswith(f"{level}_"):
            found.append(child.name)
    return found or ([level] if (server.directory / level).is_dir() else [])


def _sanitize(label: str) -> str:
    cleaned = _SAFE_LABEL.sub("-", (label or "").strip())[:40].strip("-")
    return cleaned or "backup"


def _unique_path(folder: Path, stem: str) -> Path:
    candidate = folder / f"{stem}.tar.gz"
    index = 2
    while candidate.exists() or Path(f"{candidate}.json").exists():
        candidate = folder / f"{stem}-{index}.tar.gz"
        index += 1
    return candidate


# -------------------------------------------------------------------- create


def _iter_entries(server, scope: str) -> Iterable[tuple[Path, str]]:
    root = server.directory
    if scope == "world":
        for name in world_dirs(server):
            base = root / name
            for child in base.rglob("*"):
                rel = child.relative_to(root).as_posix()
                yield child, rel
        # A world is useless without the properties that describe it.
        for extra in ("server.properties", "eula.txt"):
            if (root / extra).is_file():
                yield root / extra, extra
        return

    if scope == "config":
        for child in root.iterdir():
            if child.is_file() and child.suffix.lower() in CONFIG_SUFFIXES:
                yield child, child.name
        for pattern in ("config/*.yml", "config/*.yaml", "config/*.toml", "config/*.json"):
            for child in root.glob(pattern):
                if child.is_file():
                    yield child, child.relative_to(root).as_posix()
        return

    # full
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ALWAYS_EXCLUDE]
        for name in files:
            path = Path(base) / name
            yield path, path.relative_to(root).as_posix()


def create(server, scope: str = "world", label: str = "", progress: Progress | None = None) -> ArchiveMeta:
    """Build an archive. Blocking: call through ``asyncio.to_thread``."""
    if scope not in SCOPES:
        raise BackupError(f"未知的备份范围：{scope}")

    folder = archive_dir(server)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = _unique_path(folder, f"{stamp}-{scope}-{_sanitize(label or scope)}")

    entries = [(p, rel) for p, rel in _iter_entries(server, scope) if p.is_file()]
    if not entries:
        raise BackupError("没有可备份的内容：服务器目录还是空的，或所选范围里没有文件")

    total = len(entries)
    includes = sorted({rel.split("/", 1)[0] for _p, rel in entries})
    partial = target.with_suffix(target.suffix + ".part")
    done = 0
    try:
        with tarfile.open(partial, "w:gz", compresslevel=6) as tf:
            for path, rel in entries:
                try:
                    tf.add(path, arcname=rel, recursive=False)
                except (OSError, ValueError) as exc:
                    log.warning("skip %s while archiving: %s", rel, exc)
                done += 1
                if progress and done % 25 == 0:
                    progress(int(done * 100 / total), f"{done}/{total}")
        os.replace(partial, target)
    except Exception as exc:
        partial.unlink(missing_ok=True)
        raise BackupError(f"打包失败：{exc}") from exc

    meta = ArchiveMeta(
        name=target.name,
        scope=scope,
        label=label or scope,
        created_at=time.time(),
        bytes=target.stat().st_size,
        entries=total,
        includes=includes[:20],
    )
    _write_meta(target, meta)
    _prune(server, folder)
    if progress:
        progress(100, "完成")
    log.info("archive %s created (%d files, %.1f MB)", target.name, total, meta.bytes / 1048576)
    return meta


def _meta_path(archive: Path) -> Path:
    return archive.with_suffix(archive.suffix + ".json")


def _write_meta(archive: Path, meta: ArchiveMeta) -> None:
    payload = meta.public()
    payload["name"] = archive.name
    _meta_path(archive).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------- read


def _resolve_archive(server, name: str) -> Path:
    folder = archive_dir(server).resolve()
    if "/" in name or "\\" in name or name in {"", ".", ".."}:
        raise BackupError("非法的备份文件名")
    if not name.endswith(".tar.gz"):
        raise BackupError("非法的备份文件名")
    target = (folder / Path(name).name).resolve()
    if not target.is_relative_to(folder) or not target.is_file():
        raise BackupError("备份不存在")
    return target


def _read_meta(archive: Path) -> ArchiveMeta:
    sidecar = _meta_path(archive)
    stat = archive.stat()
    if sidecar.exists():
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            return ArchiveMeta(
                name=archive.name,
                scope=str(data.get("scope", "full")),
                label=str(data.get("label", "")),
                created_at=float(data.get("created_at", stat.st_mtime)),
                bytes=int(data.get("bytes", stat.st_size)),
                entries=int(data.get("entries", 0)),
                includes=list(data.get("includes", [])),
            )
        except (ValueError, OSError):
            pass
    # Sidecar lost: derive what we can from the name (stamp-scope-label).
    parts = archive.name.split("-", 3)
    scope = parts[2] if len(parts) > 2 and parts[2] in SCOPES else "full"
    return ArchiveMeta(
        name=archive.name,
        scope=scope,
        label=parts[3].removesuffix(".tar.gz") if len(parts) > 3 else "",
        created_at=stat.st_mtime,
        bytes=stat.st_size,
        entries=0,
        includes=[],
    )


def list_archives(server) -> list[ArchiveMeta]:
    folder = archive_dir(server)
    if not folder.exists():
        return []
    items = [_read_meta(p) for p in folder.glob("*.tar.gz") if p.is_file()]
    return sorted(items, key=lambda m: m.created_at, reverse=True)


def archive_path(server, name: str) -> Path:
    return _resolve_archive(server, name)


def delete(server, name: str) -> None:
    target = _resolve_archive(server, name)
    target.unlink(missing_ok=True)
    _meta_path(target).unlink(missing_ok=True)


def _prune(server, folder: Path) -> None:
    from ..settings import settings

    keep = max(1, int(settings.backup_keep))
    archives = sorted((p for p in folder.glob("*.tar.gz") if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in archives[keep:]:
        stale.unlink(missing_ok=True)
        _meta_path(stale).unlink(missing_ok=True)


# ------------------------------------------------------------------- restore


def _safe_members(tf: tarfile.TarFile) -> list[tarfile.TarInfo]:
    """Reject absolute paths, ``..`` traversal, links and devices."""
    members: list[tarfile.TarInfo] = []
    for member in tf.getmembers():
        name = member.name.replace("\\", "/")
        if name.startswith("/") or ".." in Path(name).parts or (len(name) > 1 and name[1] == ":"):
            raise BackupError(f"归档中包含非法路径：{member.name}")
        if member.issym() or member.islnk() or member.isdev():
            raise BackupError(f"归档中包含不支持的条目类型：{member.name}")
        members.append(member)
    return members


def restore(server, name: str, *, running: bool = False) -> dict[str, Any]:
    """Extract an archive over the server directory, with rollback on failure."""
    if running:
        raise BackupError("请先停止服务器再恢复备份：JVM 仍持有区块文件，覆盖会导致存档损坏")
    target = _resolve_archive(server, name)
    root = server.directory
    stamp = time.strftime("%Y%m%d-%H%M%S")
    staging = root / ".nautilus" / "restore" / stamp
    staging.mkdir(parents=True, exist_ok=True)

    try:
        with tarfile.open(target, "r:gz") as tf:
            members = _safe_members(tf)
            try:
                # Python >= 3.12: apply the stdlib's own safety filter too.
                tf.extractall(staging, filter="data")
            except TypeError:  # pragma: no cover - 3.10/3.11
                tf.extractall(staging)
    except BackupError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except (tarfile.TarError, OSError) as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise BackupError(f"解包失败：{exc}") from exc

    moved: list[tuple[Path, Path]] = []
    pre = root / PRERESTORE_DIRNAME / stamp
    replaced: list[str] = []
    try:
        for item in sorted(staging.iterdir()):
            destination = root / item.name
            if destination.exists():
                pre.mkdir(parents=True, exist_ok=True)
                parked = pre / item.name
                if parked.exists():
                    if parked.is_dir():
                        shutil.rmtree(parked, ignore_errors=True)
                    else:
                        parked.unlink(missing_ok=True)
                shutil.move(str(destination), str(parked))
                moved.append((destination, parked))
            shutil.move(str(item), str(destination))
            replaced.append(item.name)
    except Exception as exc:
        # Put the original tree back before surfacing the error.
        for destination, parked in reversed(moved):
            if destination.exists():
                if destination.is_dir():
                    shutil.rmtree(destination, ignore_errors=True)
                else:
                    destination.unlink(missing_ok=True)
            try:
                shutil.move(str(parked), str(destination))
            except OSError:  # pragma: no cover - best effort
                log.exception("rollback failed for %s", destination)
        shutil.rmtree(staging, ignore_errors=True)
        raise BackupError(f"恢复失败，已回滚：{exc}") from exc

    shutil.rmtree(staging, ignore_errors=True)
    log.info("restored %s into server %s (%s)", name, server.id, ", ".join(replaced))
    return {
        "ok": True,
        "restored": name,
        "replaced": replaced,
        "safety_copy": str(pre.relative_to(root)) if pre.exists() else None,
    }


def total_bytes(server) -> int:
    folder = archive_dir(server)
    if not folder.exists():
        return 0
    return sum(p.stat().st_size for p in folder.glob("*.tar.gz") if p.is_file())
