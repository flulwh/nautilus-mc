"""Minecraft server supervisor.

There is no embeddable library that owns *one Minecraft server's lifecycle*
(ready detection from the log line, player tracking, RCON-aware graceful stop,
crash back-off). This module is that domain logic; it is deliberately thin and
delegates everything else:

* RCON            -> async-mcrcon
* metrics         -> psutil
* properties/eula -> nautilus.resources.configs (jproperties)
* scheduling       -> APScheduler (see metrics.py)

The graceful-stop ladder is ported from itzg/mc-server-runner (Apache-2.0):
RCON ``stop`` -> stdin ``stop`` -> SIGTERM/CTRL_BREAK -> kill.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import signal
import socket
import subprocess
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from ..events import bus, topic
from ..models import Server
from ..settings import settings
from . import rcon as rcon_client
from .logbus import ConsoleBuffer, parse_tps

log = logging.getLogger("nautilus.supervisor")

IS_WINDOWS = os.name == "nt"
MAX_LINE = 4000
# Crash-restart bookkeeping: give up after this many consecutive crashes, and
# treat a run longer than the reset window as "healthy" so the counter clears.
AUTO_RESTART_MAX = 10
AUTO_RESTART_RESET_S = 300
#: Written next to a spawned server so a *new* panel process can recognise and
#: adopt the JVM if the old panel was upgraded or killed (shutdown_policy=keep).
RUNTIME_FILE = ".nautilus-runtime.json"
TAIL_POLL_S = 0.7


class ManagerError(RuntimeError):
    pass


@dataclass
class Runtime:
    server_id: int
    state: str = "stopped"
    proc: asyncio.subprocess.Process | None = None
    ps: psutil.Process | None = None
    console: ConsoleBuffer = field(default_factory=lambda: ConsoleBuffer(settings.max_log_lines))
    metrics: deque = field(default_factory=lambda: deque(maxlen=settings.metrics_points))
    tps: deque = field(default_factory=lambda: deque(maxlen=settings.metrics_points))
    started_at: float | None = None
    stopped_at: float | None = None
    last_exit_code: int | None = None
    expected_stop: bool = False
    restarts: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    reader: asyncio.Task | None = None
    waiter: asyncio.Task | None = None
    autorestart: asyncio.Task | None = None
    install_pct: int = -1
    cpu_prev: tuple[float, float] | None = None
    #: True when this runtime was adopted from a previous panel process: no
    #: stdin/stdout pipes, console comes from tailing ``logs/latest.log``.
    detached: bool = False
    claimed_ct: float = 0.0

    @property
    def alive(self) -> bool:
        if self.proc is not None and self.proc.returncode is None:
            return True
        return self.detached and self.ps is not None

    def _live_pid(self) -> int | None:
        if not self.alive:
            return None
        if self.proc is not None:
            return self.proc.pid
        return self.ps.pid if self.ps else None

    def snapshot(self) -> dict:
        latest = self.metrics[-1] if self.metrics else None
        return {
            "id": self.server_id,
            "state": self.state,
            "pid": self._live_pid(),
            "started_at": self.started_at,
            "uptime": round(time.time() - self.started_at) if self.started_at and self.alive else 0,
            "players": sorted(self.console.players),
            "player_count": len(self.console.players),
            "last_exit_code": self.last_exit_code,
            "restarts": self.restarts,
            "cpu": round(latest["cpu"], 1) if latest else 0.0,
            "rss_mb": round(latest["rss_mb"], 1) if latest else 0.0,
            "install_pct": self.install_pct,
            "last_error": self.console.last_error,
            "detached": self.detached,
        }


def free_port(start: int = 25565, end: int = 25665) -> int:
    for port in range(start, end):
        if not port_in_use(port):
            return port
    raise ManagerError("没有可用端口")


def port_in_use(port: int) -> bool:
    """True when something already holds ``port``.

    A plain wildcard bind is not enough on Windows: it succeeds even while
    another socket listens on ``127.0.0.1:port``. ``SO_EXCLUSIVEADDRUSE`` makes
    the kernel enforce real exclusivity there (it is the documented opposite of
    ``SO_REUSEADDR``, which we never set), and on POSIX the wildcard bind
    already conflicts with every specific-address listener.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if IS_WINDOWS and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            s.bind(("0.0.0.0", port))
        except OSError:
            return True
        return False


def _split_windows(command: str) -> list[str]:
    """Split a command line with the Windows (CommandLineToArgvW) rules.

    ``shlex.split(posix=False)`` looks like the obvious choice but keeps the
    quote characters *and* splits inside them (``-Dfoo="bar baz"`` became two
    arguments), while ``posix=True`` eats backslashes and corrupts Windows
    paths. Neither is what ``CreateProcess`` does, so the rules are applied
    here: quotes group, ``\\"`` escapes, ``""`` means a literal quote.
    """
    args: list[str] = []
    buf: list[str] = []
    in_quotes = False
    started = False
    i, n = 0, len(command)
    while i < n:
        ch = command[i]
        if ch in " \t":
            if in_quotes:
                buf.append(ch)
            elif started:
                args.append("".join(buf))
                buf, started = [], False
            i += 1
            continue
        started = True
        if ch == "\\":
            j = i
            while j < n and command[j] == "\\":
                j += 1
            count = j - i
            if j < n and command[j] == '"':
                if count % 2:  # escaped quote, not a delimiter
                    buf.append("\\" * (count // 2) + '"')
                else:
                    buf.append("\\" * (count // 2))
                    in_quotes = not in_quotes
                i = j + 1
            else:
                buf.append("\\" * count)
                i = j
        elif ch == '"':
            if in_quotes and i + 1 < n and command[i + 1] == '"':
                buf.append('"')
                i += 2
            else:
                in_quotes = not in_quotes
                i += 1
        else:
            buf.append(ch)
            i += 1
    if started:
        args.append("".join(buf))
    return args


def split_args(command: str) -> list[str]:
    """Turns the free-form argument strings from the UI into an argv list."""
    if not command.strip():
        return []
    return _split_windows(command) if IS_WINDOWS else shlex.split(command, posix=True)


# ------------------------------------------------------- runtime fingerprint file
def _runtime_file(server: Server) -> Path:
    return server.directory / RUNTIME_FILE


def _write_runtime_file(server: Server, ps: psutil.Process, pid: int, started_at: float) -> None:
    try:
        create_time = ps.create_time()
    except psutil.Error:
        create_time = 0.0
    payload = {
        "pid": pid,
        # create_time is what makes a recycled PID impossible to adopt: the
        # kernel hands out the same pid constantly, but never with the same
        # start time.
        "create_time": create_time,
        "jar": server.jar,
        "port": server.port,
        "started_at": started_at,
        "written_at": time.time(),
    }
    try:
        _runtime_file(server).write_text(json.dumps(payload), encoding="utf-8")
    except OSError as exc:  # pragma: no cover
        log.warning("cannot write %s: %s", RUNTIME_FILE, exc)


def _clear_runtime_file(server: Server) -> None:
    try:
        _runtime_file(server).unlink(missing_ok=True)
    except OSError:  # pragma: no cover
        pass


def _same_process(ps: psutil.Process | None, create_time: float) -> bool:
    if ps is None:
        return False
    try:
        return ps.is_running() and abs(ps.create_time() - create_time) < 1.0
    except psutil.Error:
        return False


def _try_process(pid: int) -> psutil.Process | None:
    try:
        return psutil.Process(pid)
    except psutil.Error:
        return None


class ServerManager:
    def __init__(self) -> None:
        self._rt: dict[int, Runtime] = {}

    # ------------------------------------------------------------------ utils
    def get(self, server_id: int) -> Runtime:
        rt = self._rt.get(server_id)
        if rt is None:
            rt = Runtime(server_id=server_id)
            self._rt[server_id] = rt
        return rt

    def has(self, server_id: int) -> bool:
        return server_id in self._rt

    def snapshot(self, server_id: int) -> dict:
        return self.get(server_id).snapshot()

    def forget(self, server_id: int) -> None:
        """Drop a deleted server's runtime, including its background tasks.

        Without this the supervisor keeps a ``Runtime`` (and the auto-restart
        task that may be sleeping in it) for every server ever created.
        """
        rt = self._rt.pop(server_id, None)
        if rt is None:
            return
        for task in (rt.reader, rt.waiter, rt.autorestart):
            if task and not task.done():
                task.cancel()

    def _publish_state(self, server_id: int, rt: Runtime) -> None:
        payload = rt.snapshot()
        bus.publish(topic(server_id, "state"), payload)
        bus.publish("global:state", {"server_id": server_id, **payload})

    # ------------------------------------------------------------------ build
    def build_command(self, server: Server) -> list[str]:
        java = server.java or settings.java
        argv = [
            java,
            f"-Xms{max(128, server.min_memory_mb)}M",
            f"-Xmx{max(256, server.memory_mb)}M",
            "-Dfile.encoding=UTF-8",
        ]
        if server.jvm_args.strip():
            argv += split_args(server.jvm_args)
        argv += ["-jar", server.jar]
        if server.server_args.strip():
            argv += split_args(server.server_args)
        return argv

    def _prepare_files(self, server: Server) -> Path:
        from ..resources.configs import set_properties

        directory = server.directory
        directory.mkdir(parents=True, exist_ok=True)
        for sub in ("plugins", "mods", "logs", "config"):
            (directory / sub).mkdir(exist_ok=True)

        if server.rcon_enabled and server.rcon_password:
            props_path = directory / "server.properties"
            values = {
                "enable-rcon": "true",
                "rcon.port": str(server.rcon_port),
                "rcon.password": server.rcon_password,
                "enable-query": "true",
                "query.port": str(server.rcon_port),
                # The database is the source of truth for the port: writing it
                # only for a fresh file let a hand-edited server-port diverge
                # from the value the panel allocates, displays and preflights.
                "server-port": str(server.port),
            }
            set_properties(props_path, values)
        elif (directory / "server.properties").exists():
            # RCON was switched off: make sure a previous run's password does
            # not keep the port open.
            set_properties(directory / "server.properties", {"enable-rcon": "false", "enable-query": "false"})

        if server.eula_accepted:
            (directory / "eula.txt").write_text(
                "# Accepted via the Nautilus panel\neula=true\n",
                encoding="utf-8",
            )
        return directory

    # ------------------------------------------------------------------ start
    async def start(self, server: Server) -> Runtime:
        rt = self.get(server.id)
        async with rt.lock:
            if rt.alive:
                return rt
            # A manual start supersedes a pending crash-restart timer.
            if rt.autorestart and not rt.autorestart.done():
                rt.autorestart.cancel()
                rt.autorestart = None
            directory = self._prepare_files(server)
            jar = directory / server.jar
            if not jar.exists():
                raise ManagerError(f"服务端 jar 不存在：{server.jar}，请先安装服务端")
            if port_in_use(server.port):
                raise ManagerError(f"端口 {server.port} 已被占用")

            argv = self.build_command(server)
            env = os.environ.copy()
            env.setdefault("JAVA_TOOL_OPTIONS", "")
            kwargs: dict = {}
            if IS_WINDOWS:
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True

            rt.state = "starting"
            rt.expected_stop = False
            rt.last_exit_code = None
            # Let the previous process' pump finish flushing before the buffer
            # is cleared, otherwise its last lines land in the new session.
            if rt.reader and not rt.reader.done():
                _done, pending = await asyncio.wait({rt.reader}, timeout=2)
                for task in pending:
                    task.cancel()
            rt.console.clear()
            rt.metrics.clear()
            rt.tps.clear()
            self._publish_state(server.id, rt)
            log.info("starting server %s: %s", server.id, " ".join(argv))

            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    cwd=str(directory),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env=env,
                    limit=1024 * 1024,
                    **kwargs,
                )
            except FileNotFoundError as exc:
                rt.state = "crashed"
                self._publish_state(server.id, rt)
                raise ManagerError(f"找不到 Java 可执行文件：{argv[0]}") from exc

            rt.proc = proc
            rt.started_at = time.time()
            rt.stopped_at = None
            try:
                rt.ps = psutil.Process(proc.pid)
            except psutil.Error:  # pragma: no cover
                rt.ps = None
            if rt.ps is not None:
                _write_runtime_file(server, rt.ps, proc.pid, rt.started_at)

            rt.reader = asyncio.create_task(self._pump(server, rt), name=f"pump-{server.id}")
            rt.waiter = asyncio.create_task(self._wait_exit(server, rt), name=f"wait-{server.id}")
            return rt

    # ------------------------------------------------------------------- pump
    async def _pump(self, server: Server, rt: Runtime) -> None:
        proc = rt.proc
        assert proc is not None and proc.stdout is not None
        log_path = server.directory / "logs" / "nautilus-console.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fh = open(log_path, "a", encoding="utf-8", buffering=1)
        except OSError:
            fh = None

        try:
            while True:
                try:
                    raw = await proc.stdout.readline()
                except (asyncio.LimitOverrunError, ValueError):
                    raw = b"[nautilus] line too long, truncated\n"
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace")[:MAX_LINE]
                parsed = rt.console.push(line)
                if fh:
                    fh.write(line)
                bus.publish(
                    topic(server.id, "log"),
                    {
                        "line": line.rstrip("\n"),
                        "level": parsed.level,
                        "stream": "stdout",
                        "players": sorted(rt.console.players),
                    },
                )
                if parsed.event == "ready" and rt.state != "running":
                    rt.state = "running"
                    rt.restarts = 0
                    rt.started_at = rt.started_at or time.time()
                    self._publish_state(server.id, rt)
                    log.info("server %s is ready", server.id)
                    from .. import notify

                    notify.emit("server.started", server_id=server.id, detail={"port": server.port})
                elif parsed.event in {"join", "leave"}:
                    bus.publish(topic(server.id, "players"), {"players": sorted(rt.console.players)})
                    if parsed.player:
                        from .. import notify

                        notify.emit(
                            f"player.{parsed.event}",
                            server_id=server.id,
                            detail={"player": parsed.player, "online": len(rt.console.players)},
                        )
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover
            log.exception("console pump failed for server %s", server.id)
        finally:
            if fh:
                fh.close()

    async def _wait_exit(self, server: Server, rt: Runtime) -> None:
        proc = rt.proc
        if proc is None:
            return
        code = await proc.wait()
        _clear_runtime_file(server)
        uptime = time.time() - rt.started_at if rt.started_at else 0
        rt.last_exit_code = code
        rt.proc = None
        rt.ps = None
        rt.stopped_at = time.time()
        rt.state = "crashed" if (code not in (0, None) and not rt.expected_stop) else "stopped"
        self._publish_state(server.id, rt)
        bus.publish(topic(server.id, "log"), {"line": f"[nautilus] 进程退出，code={code}", "level": "warn"})
        log.info("server %s exited with %s (expected=%s)", server.id, code, rt.expected_stop)

        from .. import notify

        notify.emit(
            "server.stopped" if rt.state == "stopped" else "server.crashed",
            server_id=server.id,
            detail={"code": code, "uptime": int(uptime)},
        )

        # A run that lasted a while counts as healthy: without this the crash
        # counter is exhausted after ten crashes spread over weeks and
        # auto-restart stays off forever.
        if uptime > AUTO_RESTART_RESET_S:
            rt.restarts = 0

        self._schedule_auto_restart(server, rt)

    def _schedule_auto_restart(self, server: Server, rt: Runtime) -> None:
        if not (server.auto_restart and not rt.expected_stop and rt.restarts < AUTO_RESTART_MAX):
            return
        rt.restarts += 1
        delay = min(60, 2 ** min(rt.restarts, 6))

        async def _delayed() -> None:
            await asyncio.sleep(delay)
            try:
                # A deleted/re-created server must not be resurrected by a
                # timer that belongs to the old runtime.
                if self._rt.get(server.id) is not rt:
                    return
                from ..db import SessionFactory

                async with SessionFactory() as s:
                    fresh = await s.get(Server, server.id)
                # Re-read auto_restart: the admin may have switched it off
                # while this timer was sleeping.
                if fresh and fresh.auto_restart and not rt.expected_stop:
                    await self.start(fresh)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover
                log.warning("auto restart failed: %s", exc)

        rt.autorestart = asyncio.create_task(_delayed(), name=f"autorestart-{server.id}")
        bus.publish(
            topic(server.id, "log"),
            {"line": f"[nautilus] {delay}s 后自动重启（第 {rt.restarts} 次）", "level": "warn"},
        )

    # ---------------------------------------------------------- orphan adoption
    def _owns_port(self, ps: psutil.Process, port: int) -> bool:
        try:
            return any(
                c.status == psutil.CONN_LISTEN and c.laddr and c.laddr[1] == port
                for c in ps.net_connections("tcp")  # type: ignore[attr-defined]
            )
        except (psutil.Error, OSError, AttributeError):
            return False

    def _scan_candidate(self, server: Server) -> psutil.Process | None:
        """Find an untracked JVM for this server (jar + cwd, preferring the one
        holding the game port). Covers servers started by a pre-fingerprint
        panel version, so upgrading the panel still re-adopts live servers."""
        want_dir = str(server.directory).casefold()
        matches: list[psutil.Process] = []
        for p in psutil.process_iter(["pid", "name", "cmdline", "cwd"]):
            try:
                if not (p.info["name"] or "").lower().startswith("java"):
                    continue
                if str(p.info.get("cwd") or "").casefold().rstrip("\\/") != want_dir.rstrip("\\/"):
                    continue
                argv = p.info.get("cmdline") or []
                if server.jar not in argv:
                    continue
                i = argv.index(server.jar)
                if i == 0 or argv[i - 1] != "-jar":
                    continue
                matches.append(p)
            except (psutil.Error, OSError, KeyError):
                continue
        if not matches:
            return None
        for p in matches:
            if self._owns_port(p, server.port):
                return p
        return min(matches, key=lambda p: p.pid)

    async def claim_orphans(self) -> list[int]:
        """Adopt JVMs left behind by the previous panel process.

        With ``shutdown_policy=keep`` a panel upgrade/crash leaves the servers
        running. The stdout pipe died with the old panel (Java's PrintStream
        swallows the broken pipe, so the JVM does not block or crash), which is
        why the console comes from tailing ``logs/latest.log`` and commands are
        RCON-only in this mode.
        """
        from sqlalchemy import select

        from ..db import SessionFactory

        claimed: list[int] = []
        async with SessionFactory() as session:
            servers = (await session.execute(select(Server))).scalars().all()
            for server in servers:
                path = _runtime_file(server)
                pid = 0
                create_time = 0.0
                started_at = 0.0
                fingerprint = False
                if path.is_file():
                    try:
                        meta = json.loads(path.read_text(encoding="utf-8"))
                        pid = int(meta["pid"])
                        create_time = float(meta["create_time"])
                        started_at = float(meta.get("started_at") or 0)
                        fingerprint = _same_process(_try_process(pid), create_time)
                    except (OSError, ValueError, KeyError):
                        fingerprint = False
                if not fingerprint:
                    _clear_runtime_file(server)
                    # No (valid) fingerprint: maybe an instance from before the
                    # fingerprint existed, or a panel killed before it wrote one.
                    scanned = self._scan_candidate(server)
                    if scanned is None:
                        continue
                    pid, create_time, started_at = scanned.pid, scanned.create_time(), time.time()
                    log.info("found untracked JVM for server %s (pid %s), adopting it", server.name, pid)
                rt = self.get(server.id)
                if rt.alive or rt.reader is not None:
                    continue
                ps = _try_process(pid)
                if ps is None:
                    _clear_runtime_file(server)
                    continue
                rt.detached = True
                rt.claimed_ct = create_time
                rt.proc = None
                rt.ps = ps
                rt.state = "running"
                rt.started_at = started_at or time.time()
                rt.stopped_at = None
                rt.expected_stop = False
                rt.last_exit_code = None
                self._seed_console(server, rt)
                rt.reader = asyncio.create_task(self._tail(server, rt), name=f"tail-{server.id}")
                self._publish_state(server.id, rt)
                _write_runtime_file(server, ps, pid, rt.started_at)
                claimed.append(server.id)
                log.info("adopted orphan of server %s (pid %s, port %s)", server.name, pid, server.port)
        return claimed

    def _seed_console(self, server: Server, rt: Runtime) -> None:
        """Rebuild the visible console (and the online-players set) from the file."""
        try:
            raw = (server.directory / "logs" / "latest.log").read_bytes()[-512 * 1024 :]
        except OSError:
            raw = b""
        for line in raw.decode("utf-8", errors="replace").splitlines()[-settings.max_log_lines :]:
            rt.console.push(line[:MAX_LINE])

    async def _tail(self, server: Server, rt: Runtime) -> None:
        path = server.directory / "logs" / "latest.log"
        offset = 0
        try:
            offset = path.stat().st_size
        except OSError:
            pass
        lost = False
        try:
            while True:
                await asyncio.sleep(TAIL_POLL_S)
                try:
                    size = path.stat().st_size
                except OSError:
                    size = 0
                if size < offset:  # daily rotation or truncation: restart at 0
                    offset = 0
                if size > offset:
                    try:
                        with path.open("rb") as fh:
                            fh.seek(offset)
                            chunk = fh.read(size - offset)
                            offset = fh.tell()
                    except OSError:  # pragma: no cover
                        continue
                    for raw in chunk.decode("utf-8", errors="replace").splitlines():
                        line = raw[:MAX_LINE]
                        parsed = rt.console.push(line)
                        bus.publish(
                            topic(server.id, "log"),
                            {
                                "line": line.rstrip("\r"),
                                "level": parsed.level,
                                "stream": "log",
                                "players": sorted(rt.console.players),
                            },
                        )
                        if parsed.event == "ready" and rt.state != "running":
                            rt.state = "running"
                            self._publish_state(server.id, rt)
                        elif parsed.event in {"join", "leave"}:
                            bus.publish(topic(server.id, "players"), {"players": sorted(rt.console.players)})
                if not _same_process(rt.ps, rt.claimed_ct):
                    lost = True
                    break
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover
            log.exception("console tail failed for server %s", server.id)
        if lost and rt.detached and not rt.expected_stop:
            # The adopted JVM died on its own: report it like an unclean exit.
            rt.detached = False
            rt.ps = None
            rt.state = "crashed"
            rt.stopped_at = time.time()
            rt.last_exit_code = None
            _clear_runtime_file(server)
            self._publish_state(server.id, rt)
            bus.publish(topic(server.id, "log"), {"line": "[nautilus] 接管的进程已消失", "level": "error"})
            from .. import notify

            notify.emit("server.crashed", server_id=server.id, detail={"code": None, "detached": True})
            self._schedule_auto_restart(server, rt)

    async def _kill_pid_tree(self, pid: int) -> None:
        if IS_WINDOWS:
            try:
                await (
                    await asyncio.create_subprocess_exec(
                        "taskkill",
                        "/F",
                        "/T",
                        "/PID",
                        str(pid),
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                ).wait()
            except OSError:  # pragma: no cover
                pass
            return
        try:
            parent = psutil.Process(pid)
        except psutil.Error:
            return
        for child in parent.children(recursive=True):
            try:
                child.terminate()
            except psutil.Error:
                pass
        try:
            parent.terminate()
            parent.wait(5)
        except psutil.NoSuchProcess:
            pass
        except psutil.TimeoutExpired:
            for proc in [parent, *parent.children(recursive=True)]:
                try:
                    proc.kill()
                except psutil.Error:
                    pass

    async def _stop_detached(self, server: Server, rt: Runtime, *, force: bool) -> None:
        pid = rt.ps.pid if rt.ps is not None else None
        if not force and server.rcon_enabled and server.rcon_password and pid is not None:
            try:
                await rcon_client.command("127.0.0.1", server.rcon_port, server.rcon_password, "stop", timeout=5)
                log.info("sent RCON stop to adopted server %s", server.id)
            except rcon_client.RconError as exc:
                log.debug("rcon stop unavailable: %s", exc)
        deadline = time.monotonic() + (2 if force else min(settings.stop_grace_seconds, 30))
        while time.monotonic() < deadline:
            if not _same_process(rt.ps, rt.claimed_ct):
                break
            await asyncio.sleep(0.3)
        if _same_process(rt.ps, rt.claimed_ct) and pid is not None:
            await self._kill_pid_tree(pid)
        if rt.reader is not None and not rt.reader.done():
            rt.reader.cancel()
        rt.detached = False
        rt.ps = None
        rt.stopped_at = time.time()
        rt.last_exit_code = None
        _clear_runtime_file(server)
        rt.state = "stopped"
        self._publish_state(server.id, rt)
        from .. import notify

        notify.emit("server.stopped", server_id=server.id, detail={"code": None, "detached": True})

    # ------------------------------------------------------------------- stop
    def _settle(self, rt: Runtime) -> None:
        """Make sure a dead process never leaves the state at ``stopping``.

        The exit waiter normally publishes the final state, but if that task
        ever ends early (exception, cancellation) the dashboard would spin on
        "stopping" forever after the process is already gone.
        """
        if rt.alive or rt.state != "stopping":
            return
        rt.ps = None
        rt.stopped_at = rt.stopped_at or time.time()
        rt.state = "stopped"
        self._publish_state(rt.server_id, rt)

    async def _wait_gone(self, rt: Runtime, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not rt.alive:
                self._settle(rt)
                return True
            await asyncio.sleep(0.25)
        if not rt.alive:
            self._settle(rt)
        return not rt.alive

    async def stop(self, server: Server, *, force: bool = False) -> Runtime:
        rt = self.get(server.id)
        async with rt.lock:
            if not rt.alive:
                rt.state = "stopped"
                self._publish_state(server.id, rt)
                return rt

            rt.expected_stop = True
            # An explicit stop must win over a queued crash-restart.
            if rt.autorestart and not rt.autorestart.done():
                rt.autorestart.cancel()
                rt.autorestart = None
            was_running = rt.state == "running"
            rt.state = "stopping"
            self._publish_state(server.id, rt)
            if rt.detached:
                await self._stop_detached(server, rt, force=force)
                return rt
            proc = rt.proc
            assert proc is not None

            if force:
                await self._hard_kill(rt, proc)
                await self._wait_gone(rt, 5)
                return rt

            # 1) RCON stop (works even when stdin is detached)
            if was_running and server.rcon_enabled and server.rcon_password:
                try:
                    await rcon_client.command("127.0.0.1", server.rcon_port, server.rcon_password, "stop", timeout=5)
                    log.info("sent RCON stop to server %s", server.id)
                except rcon_client.RconError as exc:
                    log.debug("rcon stop unavailable: %s", exc)

            if await self._wait_gone(rt, min(settings.stop_grace_seconds, 30)):
                return rt

            # 2) stdin stop
            if proc.stdin and not proc.stdin.is_closing():
                try:
                    proc.stdin.write(b"stop\n")
                    await proc.stdin.drain()
                except (ConnectionResetError, BrokenPipeError, OSError):
                    pass
            if await self._wait_gone(rt, 15):
                return rt

            # 3) polite signal
            try:
                if IS_WINDOWS:
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    proc.send_signal(signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
            if await self._wait_gone(rt, 15):
                return rt

            # 4) kill
            await self._hard_kill(rt, proc)
            await self._wait_gone(rt, 5)
            return rt

    async def _hard_kill(self, rt: Runtime, proc: asyncio.subprocess.Process) -> None:
        if IS_WINDOWS:
            try:
                await (await asyncio.create_subprocess_exec(
                    "taskkill", "/F", "/T", "/PID", str(proc.pid),
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                )).wait()
            except OSError:  # pragma: no cover
                pass
        try:
            proc.kill()
        except ProcessLookupError:
            pass

    async def restart(self, server: Server) -> Runtime:
        await self.stop(server)
        await asyncio.sleep(0.5)
        return await self.start(server)

    # --------------------------------------------------------------- commands
    async def send(self, server: Server, command: str) -> str:
        rt = self.get(server.id)
        if not rt.alive or rt.state not in {"running", "starting"}:
            raise ManagerError("服务器未运行")
        command = command.strip().lstrip("/")
        if not command:
            raise ManagerError("指令为空")
        if server.rcon_enabled and server.rcon_password and rt.state == "running":
            try:
                return await rcon_client.command("127.0.0.1", server.rcon_port, server.rcon_password, command)
            except rcon_client.RconError as exc:
                log.debug("rcon command failed, falling back to stdin: %s", exc)
        proc = rt.proc
        if rt.detached:
            raise ManagerError("接管模式下无法写 stdin（原管道已随旧面板关闭），请为该服务器启用 RCON")
        if proc is None or proc.stdin is None or proc.stdin.is_closing():
            raise ManagerError("控制台不可写")
        # Console commands may arrive from HTTP requests, scheduled tasks and
        # the WebSocket at the same time; interleaved writes would corrupt the
        # line the server reads.
        async with rt.write_lock:
            try:
                proc.stdin.write((command + "\n").encode("utf-8"))
                await proc.stdin.drain()
            except (ConnectionResetError, BrokenPipeError, OSError) as exc:
                raise ManagerError(f"控制台写入失败：{exc}") from exc
        return ""

    # ---------------------------------------------------------------- metrics
    def sample(self) -> None:
        for sid, rt in list(self._rt.items()):
            if not rt.alive or rt.ps is None:
                continue
            # On Windows java.exe is a launcher: the JVM (and the real memory
            # footprint) lives in a child process, so the whole tree is summed.
            try:
                procs = [rt.ps, *rt.ps.children(recursive=True)]
            except psutil.Error:
                procs = [rt.ps]
            cpu_seconds = mem = threads = 0.0
            counted = 0
            for proc in procs:
                try:
                    times = proc.cpu_times()
                    cpu_seconds += times.user + times.system
                    mem += proc.memory_info().rss / (1024 * 1024)
                    threads += proc.num_threads()
                    counted += 1
                except psutil.Error:
                    continue
            if not counted:
                continue
            # CPU% from cumulative CPU-time deltas: stable across processes that
            # are re-created (children) and independent of psutil's internal state.
            # The *monotonic* clock is used for the delta: ``time.time()`` can
            # jump backwards on an NTP correction and would produce a bogus
            # spike (or a permanent zero) for one sample.
            now = time.monotonic()
            cpu = 0.0
            if rt.cpu_prev is not None:
                dt = now - rt.cpu_prev[0]
                if dt > 0:
                    cpu = max(0.0, (cpu_seconds - rt.cpu_prev[1]) / dt * 100)
            rt.cpu_prev = (now, cpu_seconds)
            point = {
                "ts": time.time(),
                "cpu": cpu,
                "rss_mb": mem,
                "threads": int(threads),
                "players": len(rt.console.players),
            }
            rt.metrics.append(point)
            bus.publish(topic(sid, "metrics"), point)

    # -------------------------------------------------------------------- tps
    async def sample_tps(self) -> None:
        """Poll ``/tps`` over RCON for running Paper-family servers.

        Requires RCON: the stdin fallback returns before the answer is
        produced, and correlating async console output with a command would
        race with whatever else is typing into the console.
        """
        from ..db import SessionFactory

        running = [(sid, rt) for sid, rt in self._rt.items() if rt.alive and rt.state == "running"]
        if not running:
            return
        async with SessionFactory() as session:
            for sid, rt in running:
                server = await session.get(Server, sid)
                if server is None or server.kind not in {"paper", "purpur"}:
                    continue
                if not server.rcon_enabled or not server.rcon_password:
                    continue
                try:
                    output = await self.send(server, "tps")
                except (ManagerError, rcon_client.RconError, OSError, asyncio.TimeoutError):
                    continue
                parsed = parse_tps(output) if output else None
                if not parsed:
                    continue
                point = {"ts": time.time(), **parsed}
                rt.tps.append(point)
                bus.publish(topic(sid, "tps"), point)

    # --------------------------------------------------------------- shutdown
    async def shutdown(self) -> None:
        keep = settings.shutdown_policy == "keep"
        tasks = []
        for sid, rt in list(self._rt.items()):
            if rt.alive:
                if keep:
                    continue
                from ..db import SessionFactory

                async with SessionFactory() as s:
                    server = await s.get(Server, sid)
                if server:
                    tasks.append(self.stop(server))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for rt in self._rt.values():
            for t in (rt.reader, rt.waiter, rt.autorestart):
                if t and not t.done():
                    t.cancel()
        if keep:
            kept = sum(1 for rt in self._rt.values() if rt.alive)
            if kept:
                log.info("shutdown_policy=keep: %d server process(es) stay running and will be re-adopted", kept)


manager = ServerManager()
