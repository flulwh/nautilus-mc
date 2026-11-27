"""Java Flight Recorder control through ``jcmd``.

Since JDK 11 JFR is free and needs no launch flags: ``jcmd <pid> JFR.start``
works against an already-running HotSpot. The catch is that ``jcmd`` ships
with the JDK, not the JRE — the managed runtime download therefore fetches
full JDKs, and the sibling binary of the *actual* java used to boot the
server is the one we call.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import time
from pathlib import Path

from ..models import Server
from ..settings import settings

log = logging.getLogger("nautilus.jfr")

JFR_PREFIX = "nautilus-jfr-"
SETTINGS_NAMES = {"default", "profile"}


class JfrError(RuntimeError):
    pass


def jcmd_path(server: Server) -> str | None:
    """Locate the ``jcmd`` belonging to the runtime that boots this server."""
    exe = "jcmd.exe" if os.name == "nt" else "jcmd"
    configured = Path(server.java or settings.java)
    bases = []
    if configured.parent and str(configured.parent) != ".":
        bases.append(configured.parent)
    try:
        resolved = configured.resolve()
        bases.append(resolved.parent)
    except OSError:
        pass
    for base in bases:
        candidate = base / exe
        if candidate.is_file():
            return str(candidate)
    return shutil.which("jcmd")


def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


async def _run(jcmd: str, pid: int, *args: str) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            jcmd,
            str(pid),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=45)
    except (OSError, asyncio.TimeoutError) as exc:
        raise JfrError(f"jcmd 执行失败：{exc}") from exc
    text = out.decode("utf-8", errors="replace").strip()
    lowered = text.lower()
    if proc.returncode != 0 or "attachnotsupportedexception" in lowered or "exception:" in lowered:
        raise JfrError(text or f"jcmd 退出码 {proc.returncode}")
    return text


_RE_RECORDING = re.compile(r"^Recording\s+\d+:\s*name=([\w.\-]+)(.*)$")
_RE_LEGACY = re.compile(r"^([\w.\-]+):\s*(\(?(?:running|stopped)\)?.*)$", re.IGNORECASE)
_RE_STATE = re.compile(r"\((running|stopped)\)", re.IGNORECASE)


def parse_check(text: str) -> list[dict]:
    """Parse ``jcmd <pid> JFR.check`` into [{name, running, detail}].

    JDK 21 prints ``Recording 1: name=x duration=30s (running)``; older builds
    used ``name: (running) ...``. Accept both.
    """
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        m = _RE_RECORDING.match(line)
        if m:
            name, rest = m.group(1), m.group(2)
        else:
            m = _RE_LEGACY.match(line)
            if not m or not _RE_STATE.search(m.group(2)):
                continue
            name, rest = m.group(1), m.group(2)
        state = _RE_STATE.search(rest)
        if not state:
            continue
        out.append(
            {
                "name": name,
                "running": state.group(1).lower() == "running",
                "detail": _RE_STATE.sub("", rest).strip(),
            }
        )
    return out


def _target_file(server: Server, filename: str) -> Path:
    if not re.fullmatch(rf"{re.escape(JFR_PREFIX)}[\w.\-]+\.jfr", filename):
        raise JfrError("非法的剖析文件名")
    return server.directory / "logs" / filename


def list_dumps(server: Server) -> list[dict]:
    logs = server.directory / "logs"
    dumps = []
    if logs.is_dir():
        for f in sorted(logs.glob(f"{JFR_PREFIX}*.jfr"), key=lambda p: p.stat().st_mtime, reverse=True):
            st = f.stat()
            dumps.append({"filename": f.name, "size": st.st_size, "modified": st.st_mtime})
    return dumps


def _prune(server: Server) -> None:
    """Keep only ``settings.jfr_keep`` newest recordings on disk."""
    for dump in list_dumps(server)[settings.jfr_keep :]:
        try:
            _target_file(server, dump["filename"]).unlink(missing_ok=True)
        except OSError as exc:
            log.debug("jfr prune failed for %s: %s", dump["filename"], exc)


async def check(server: Server, pid: int) -> list[dict]:
    jcmd = jcmd_path(server)
    if not jcmd:
        raise JfrError("当前 Java 运行环境没有 jcmd，请为服务器选择 JDK（面板可在线下载）")
    return parse_check(await _run(jcmd, pid, "JFR.check"))


async def start(server: Server, pid: int, duration_s: int, profile: str) -> dict:
    jcmd = jcmd_path(server)
    if not jcmd:
        raise JfrError("当前 Java 运行环境没有 jcmd，请为服务器选择 JDK（面板可在线下载）")
    if profile not in SETTINGS_NAMES:
        raise JfrError(f"剖析配置仅支持 {', '.join(sorted(SETTINGS_NAMES))}")
    name = f"nautilus-{_stamp()}"
    filename = f"{JFR_PREFIX}{name.removeprefix('nautilus-')}.jfr"
    target = _target_file(server, filename)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Quoted: jcmd re-parses its arguments as a command line, so a data dir
    # with spaces (C:\Users\My Name\...) would otherwise truncate the path.
    await _run(
        jcmd,
        pid,
        "JFR.start",
        f"name={name}",
        f"duration={max(5, min(int(duration_s), 3600))}s",
        f"settings={profile}",
        f'filename="{target.as_posix()}"',
    )
    _prune(server)
    return {"name": name, "filename": filename}


async def dump(server: Server, pid: int, name: str = "") -> dict:
    jcmd = jcmd_path(server)
    if not jcmd:
        raise JfrError("当前 Java 运行环境没有 jcmd，请为服务器选择 JDK（面板可在线下载）")
    # HotSpot keeps no snapshot buffer of its own: without an in-flight
    # recording JFR.dump exits 0 with "No recordings to dump from" and writes
    # nothing, so pre-check and dump the newest running recording.
    running = [r["name"] for r in parse_check(await _run(jcmd, pid, "JFR.check")) if r["running"]]
    if not running:
        raise JfrError("当前没有进行中的录制，请先点「录制」开始一段，再随时快照")
    filename = f"{JFR_PREFIX}{_stamp()}-dump.jfr"
    target = _target_file(server, filename)
    target.parent.mkdir(parents=True, exist_ok=True)
    out = await _run(
        jcmd,
        pid,
        "JFR.dump",
        f'filename="{target.as_posix()}"',
        f"name={name or running[0]}",
    )
    if "no recordings to dump" in out.lower():
        raise JfrError("录制恰好结束了，快照未写入，请重试")
    _prune(server)
    return {"filename": filename}


async def stop(server: Server, pid: int, name: str) -> dict:
    jcmd = jcmd_path(server)
    if not jcmd:
        raise JfrError("当前 Java 运行环境没有 jcmd，请为服务器选择 JDK（面板可在线下载）")
    if not re.fullmatch(r"[\w.\-]+", name):
        raise JfrError("非法的录制名称")
    await _run(jcmd, pid, "JFR.stop", f"name={name}")
    return {"stopped": name}


def remove_dump(server: Server, filename: str) -> None:
    _target_file(server, filename).unlink(missing_ok=True)
