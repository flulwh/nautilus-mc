"""Sandboxed file browser for a server directory (uses aiofiles for IO)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import aiofiles

from .configs import detect_format

MAX_READ = 2 * 1024 * 1024
HIDDEN_OK = {".nautilus"}


class FileError(RuntimeError):
    pass


def resolve(root: Path, rel: str) -> Path:
    """Resolve ``rel`` inside ``root``, refusing traversal and symlink escapes."""
    rel = (rel or "").strip().lstrip("/\\")
    candidate = (root / rel).resolve()
    root_resolved = root.resolve()
    if not candidate.is_relative_to(root_resolved):
        raise FileError("路径越界，已拒绝访问")
    return candidate


def _entry(path: Path, root: Path) -> dict:
    st = path.stat()
    return {
        "name": path.name,
        "path": path.relative_to(root).as_posix(),
        "is_dir": path.is_dir(),
        "size": 0 if path.is_dir() else st.st_size,
        "mtime": st.st_mtime,
        "editable": (not path.is_dir()) and st.st_size <= MAX_READ and path.suffix.lower() not in {".jar", ".zip", ".gz", ".tar", ".dat", ".mca"},
        "format": detect_format(path) if path.is_file() else "",
    }


def listing(root: Path, rel: str = "", show_hidden: bool = False) -> dict:
    target = resolve(root, rel)
    if not target.exists():
        raise FileError("目录不存在")
    if target.is_file():
        return {"path": rel, "entries": [_entry(target, root)]}
    entries: list[dict] = []
    for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if not show_hidden and child.name.startswith(".") and child.name not in HIDDEN_OK:
            continue
        try:
            entries.append(_entry(child, root))
        except OSError:
            continue
    return {
        "path": target.relative_to(root.resolve()).as_posix() if target != root.resolve() else "",
        "entries": entries,
        "parent": target.parent.relative_to(root.resolve()).as_posix() if target != root.resolve() else None,
    }


async def read_text(path: Path) -> str:
    if not path.is_file():
        raise FileError("不是文件")
    if path.stat().st_size > MAX_READ:
        raise FileError("文件超过 2MB，请下载后查看")
    async with aiofiles.open(path, "rb") as fh:
        raw = await fh.read()
    if b"\x00" in raw[:4096]:
        raise FileError("二进制文件无法编辑")
    return raw.decode("utf-8", errors="replace")


async def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    async with aiofiles.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        await fh.write(text)
    os.replace(tmp, path)


async def save_upload(path: Path, upload) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    async with aiofiles.open(path, "wb") as fh:
        while chunk := await upload.read(1024 * 256):
            await fh.write(chunk)
            size += len(chunk)
    return size


def mkdir(root: Path, rel: str) -> None:
    target = resolve(root, rel)
    target.mkdir(parents=True, exist_ok=True)


def rename(root: Path, rel: str, new_name: str) -> str:
    src = resolve(root, rel)
    if "/" in new_name or "\\" in new_name or new_name in {".", ".."}:
        raise FileError("新名称不能包含路径分隔符")
    dst = src.with_name(new_name)
    if dst.exists():
        raise FileError("目标已存在")
    src.replace(dst)
    return dst.relative_to(root.resolve()).as_posix()


def delete(root: Path, rel: str, recursive: bool = False) -> None:
    target = resolve(root, rel)
    if target == root.resolve():
        raise FileError("不能删除服务器根目录")
    if target.is_dir():
        if recursive:
            shutil.rmtree(target)
        else:
            target.rmdir()
    else:
        target.unlink(missing_ok=True)


def tree_size(root: Path) -> int:
    total = 0
    for base, _dirs, files in os.walk(root):
        for name in files:
            try:
                total += (Path(base) / name).stat().st_size
            except OSError:
                continue
    return total
