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
import logging
import os
import secrets
import shlex
import signal
import socket
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from ..events import bus, topic
from ..models import Server
from ..settings import settings
from . import rcon as rcon_client
from .logbus import ConsoleBuffer

log = logging.getLogger("nautilus.supervisor")

IS_WINDOWS = os.name == "nt"
MAX_LINE = 4000


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
    started_at: float | None = None
    stopped_at: float | None = None
    last_exit_code: int | None = None
    expected_stop: bool = False
    restarts: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    reader: asyncio.Task | None = None
    waiter: asyncio.Task | None = None
    install_pct: int = -1
    cpu_prev: tuple[float, float] | None = None

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    def snapshot(self) -> dict:
        latest = self.metrics[-1] if self.metrics else None
        return {
            "id": self.server_id,
            "state": self.state,
            "pid": self.proc.pid if self.alive else None,
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
        }


def free_port(start: int = 25565, end: int = 25665) -> int:
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    raise ManagerError("没有可用端口")


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


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
            argv += shlex.split(server.jvm_args, posix=not IS_WINDOWS)
        argv += ["-jar", server.jar]
        if server.server_args.strip():
            argv += shlex.split(server.server_args, posix=not IS_WINDOWS)
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
            }
            if not props_path.exists():
                values["server-port"] = str(server.port)
            set_properties(props_path, values)

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
            rt.console.clear()
            rt.metrics.clear()
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
                elif parsed.event in {"join", "leave"}:
                    bus.publish(topic(server.id, "players"), {"players": sorted(rt.console.players)})
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
        rt.last_exit_code = code
        rt.proc = None
        rt.ps = None
        rt.stopped_at = time.time()
        auto = server.auto_restart and not rt.expected_stop
        rt.state = "crashed" if (code not in (0, None) and not rt.expected_stop) else "stopped"
        self._publish_state(server.id, rt)
        bus.publish(topic(server.id, "log"), {"line": f"[nautilus] 进程退出，code={code}", "level": "warn"})
        log.info("server %s exited with %s (expected=%s)", server.id, code, rt.expected_stop)

        if auto and rt.restarts < 10:
            rt.restarts += 1
            delay = min(60, 2 ** min(rt.restarts, 6))
            bus.publish(
                topic(server.id, "log"),
                {"line": f"[nautilus] {delay}s 后自动重启（第 {rt.restarts} 次）", "level": "warn"},
            )

            async def _delayed() -> None:
                await asyncio.sleep(delay)
                try:
                    from ..db import SessionFactory

                    async with SessionFactory() as s:
                        fresh = await s.get(Server, server.id)
                    if fresh and not rt.expected_stop:
                        await self.start(fresh)
                except Exception as exc:  # pragma: no cover
                    log.warning("auto restart failed: %s", exc)

            asyncio.create_task(_delayed(), name=f"autorestart-{server.id}")

    # ------------------------------------------------------------------- stop
    async def _wait_gone(self, rt: Runtime, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not rt.alive:
                return True
            await asyncio.sleep(0.25)
        return not rt.alive

    async def stop(self, server: Server, *, force: bool = False) -> Runtime:
        rt = self.get(server.id)
        async with rt.lock:
            if not rt.alive:
                rt.state = "stopped"
                self._publish_state(server.id, rt)
                return rt

            rt.expected_stop = True
            was_running = rt.state == "running"
            rt.state = "stopping"
            self._publish_state(server.id, rt)
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
        if proc is None or proc.stdin is None or proc.stdin.is_closing():
            raise ManagerError("控制台不可写")
        proc.stdin.write((command + "\n").encode("utf-8"))
        await proc.stdin.drain()
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
            now = time.time()
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

    # --------------------------------------------------------------- shutdown
    async def shutdown(self) -> None:
        tasks = []
        for sid, rt in list(self._rt.items()):
            if rt.alive:
                from ..db import SessionFactory

                async with SessionFactory() as s:
                    server = await s.get(Server, sid)
                if server:
                    tasks.append(self.stop(server))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for rt in self._rt.values():
            for t in (rt.reader, rt.waiter):
                if t and not t.done():
                    t.cancel()


manager = ServerManager()
