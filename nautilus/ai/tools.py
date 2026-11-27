"""把面板能力封装成工具。

设计约束（也是这个文件存在的理由）：

* **不认识 pydantic-ai**，也不认识 FastAPI。工具就是普通 async 函数，
  所以面板内置对话和 ``nautilus.mcp_server`` 可以共用同一份实现。
* **权限不旁路**：每个工具声明自己需要的 ``(obj, act)``，走与 HTTP 路由
  完全相同的 Casbin 策略。AI 能做的事永远不会超过调用它的那个用户。
* **返回结构统一**：成功 ``{"ok": true, ...}``，失败 ``{"ok": false, "error": ...}``。
  模型看得懂，MCP 客户端也看得懂。
* **危险操作单独标记**：``dangerous=True`` 的工具在“需确认”模式下会暂停，
  等人点头再执行（删除、覆盖、强杀这类不可逆动作）。
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import Any

from sqlalchemy import select

from .. import audit
from ..authz import authz
from ..db import session_scope
from ..models import ScheduledTask, Server
from ..resources import backups as backup_service
from ..resources import configs as config_service
from ..resources import files as file_service
from ..resources import players as player_service
from ..resources import plugins as plugin_service
from ..resources import worlds as world_service
from ..servers import java as java_service
from ..servers import ops
from ..servers import provision
from ..servers import tasks as task_service
from ..servers.manager import ManagerError, manager
from ..settings import settings
from . import plan as plan_service
from . import playbooks
from .plan import StepIn

log = logging.getLogger("nautilus.ai.tools")

#: 单个工具一次性返回给模型的上限，防止一段日志把上下文塞爆。
MAX_CHARS = 24_000


# --------------------------------------------------------------------------
# 执行上下文
# --------------------------------------------------------------------------
@dataclass
class Ctx:
    """一次工具调用“以谁的身份”发生。"""

    role: str = "admin"
    user_id: int | None = None
    username: str = "system"
    ip: str = ""
    #: panel = 面板内置对话，mcp = 外部 agent 经 MCP 调用。
    source: str = "panel"
    #: 会话 key（已按用户隔离）。任务清单按它存取，避免不同对话互相覆盖。
    conversation_id: str = ""


#: 没有默认值：忘了 set_ctx 就应当报错，而不是默默以 admin 身份跑。
_ctx: ContextVar[Ctx | None] = ContextVar("nautilus_ai_ctx", default=None)


def set_ctx(ctx: Ctx):
    return _ctx.set(ctx)


def reset_ctx(token) -> None:
    _ctx.reset(token)


def current_ctx() -> Ctx:
    ctx = _ctx.get()
    if ctx is None:
        raise RuntimeError("AI 工具缺少执行身份：调用前必须先 set_ctx()")
    return ctx


class ToolError(RuntimeError):
    """可预期的业务错误，直接回给模型当结果，不当作故障。"""


class PermissionDenied(ToolError):
    pass


def _require(obj: str, act: str) -> None:
    if not authz.can(current_ctx().role, obj, act):
        raise PermissionDenied(f"缺少权限 {obj}:{act}")


# --------------------------------------------------------------------------
# 注册表
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ToolSpec:
    fn: Callable[..., Any]
    name: str
    summary: str
    perm: tuple[str, str] | None
    dangerous: bool
    mutates: bool
    #: pydantic-ai 的工具超时；None 由 build_agent 按类别兜底。
    timeout: float | None


TOOLS: dict[str, ToolSpec] = {}


def _summary(fn: Callable[..., Any]) -> str:
    doc = (inspect.getdoc(fn) or "").strip().splitlines()
    return doc[0].strip() if doc else fn.__name__


def tool(
    *,
    perm: tuple[str, str] | None = None,
    dangerous: bool = False,
    mutates: bool = False,
    timeout: float | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """注册一个工具。

    包装层统一负责：权限校验 / 异常翻译 / 审计。工具函数本身只写业务。
    """

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            ctx: Ctx | None = None
            try:
                ctx = current_ctx()
                if perm is not None:
                    _require(*perm)
                result = await fn(*args, **kwargs)
            except PermissionDenied as exc:
                who = f"{ctx.username}({ctx.role})" if ctx else "未知身份"
                return {"ok": False, "error": f"{exc}。当前身份 {who}，请让有权限的用户操作。"}
            except ToolError as exc:
                return {"ok": False, "error": str(exc)}
            except Exception as exc:
                log.exception("AI 工具 %s 执行失败", fn.__name__)
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

            payload = dict(result) if isinstance(result, dict) else {"result": result}
            payload.setdefault("ok", True)
            if mutates and ctx is not None:
                await _audit(fn.__name__, args, kwargs, payload.get("ok", True), ctx)
            return payload

        # pydantic-ai 和 MCP 都靠签名生成 schema，包装后必须把原签名带回去。
        wrapper.__signature__ = inspect.signature(fn)  # type: ignore[attr-defined]
        TOOLS[fn.__name__] = ToolSpec(
            fn=wrapper,
            name=fn.__name__,
            summary=_summary(fn),
            perm=perm,
            dangerous=dangerous,
            mutates=mutates,
            timeout=timeout,
        )
        return wrapper

    return deco


async def _audit(name: str, args: tuple, kwargs: dict, ok: bool, ctx: Ctx) -> None:
    detail = {"tool": name, "source": ctx.source}
    try:
        bound = inspect.signature(TOOLS[name].fn).bind(*args, **kwargs)
        bound.apply_defaults()
        detail["args"] = {k: _brief(v) for k, v in bound.arguments.items()}
    except (TypeError, ValueError):
        pass
    await audit.record(
        None,
        action=f"ai.{name}",
        actor=ctx.username,
        target=detail["args"].get("server_id", "") if "args" in detail else "",
        detail=detail,
        ip=ctx.ip,
        ok=ok,
    )


def _brief(value: Any, limit: int = 120) -> Any:
    if isinstance(value, (str, bytes)):
        text = value.decode(errors="replace") if isinstance(value, bytes) else value
        return text if len(text) <= limit else text[:limit] + "..."
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_brief(v, 40) for v in value[:8]]
    if isinstance(value, dict):
        return {str(k): _brief(v, 40) for k, v in list(value.items())[:8]}
    return str(value)[:limit]


def catalogue() -> list[dict[str, Any]]:
    """工具清单，前端用来展示“它能做什么”。"""
    return [
        {
            "name": spec.name,
            "summary": spec.summary,
            "dangerous": spec.dangerous,
            "mutates": spec.mutates,
            "perm": f"{spec.perm[0]}:{spec.perm[1]}" if spec.perm else "",
        }
        for spec in TOOLS.values()
    ]


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------
async def _server(session, server_id: int) -> Server:
    row = await session.get(Server, server_id)
    if row is None:
        raise ToolError(f"服务器 {server_id} 不存在，先用 list_servers 查 id")
    return row


def _clip(text: str, limit: int = MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...（已截断，原文 {len(text)} 字符）"


def _snap(server_id: int) -> dict[str, Any]:
    return manager.snapshot(server_id)


# ==========================================================================
# 服务器
# ==========================================================================
@tool(perm=("server", "read"))
async def list_servers() -> dict[str, Any]:
    """列出所有 Minecraft 服务器（id、名称、类型、版本、运行状态、端口）。调其它工具前先用它拿 id。"""
    async with session_scope() as s:
        rows = (await s.execute(select(Server).order_by(Server.id))).scalars().all()
        items = []
        for row in rows:
            snap = _snap(row.id)
            items.append(
                {
                    "id": row.id,
                    "name": row.name,
                    "kind": row.kind,
                    "version": row.version,
                    "state": snap.get("state"),
                    "port": row.port,
                    "public_address": row.public_address or f"本机:{row.port}",
                    "players_online": len(snap.get("players") or []),
                    "memory_mb": row.memory_mb,
                }
            )
    return {"servers": items, "count": len(items)}


@tool(perm=("server", "read"))
async def server_status(server_id: int) -> dict[str, Any]:
    """查看单个服务器的详细状态：配置、进程、CPU/内存采样、在线玩家、日志最后一行。"""
    async with session_scope() as s:
        row = await _server(s, server_id)
        info = row.public()
        info["jar_ready"] = (row.directory / row.jar).exists()
    snap = _snap(server_id)
    info["state"] = snap.get("state")
    info["pid"] = snap.get("pid")
    info["uptime"] = snap.get("uptime")
    info["players"] = snap.get("players") or []
    metrics = list(snap.get("metrics") or [])
    if metrics:
        last = metrics[-1]
        info["cpu_percent"] = round(float(last.get("cpu", 0)), 1)
        info["rss_mb"] = round(float(last.get("rss_mb", 0)), 1)
    lines = manager.get(server_id).console.snapshot(5)
    info["last_log"] = [item.get("line", "") for item in lines[-5:]]
    return {"server": info}


@tool(perm=("server", "control"), mutates=True)
async def server_start(server_id: int) -> dict[str, Any]:
    """启动服务器。已在运行时直接返回当前状态，不会重复启动。"""
    async with session_scope() as s:
        row = await _server(s, server_id)
        snap = _snap(server_id)
        if snap.get("state") in {"running", "starting"}:
            return {"state": snap.get("state"), "note": "服务器已在运行"}
        await manager.start(row)
        return {"state": "starting", "note": "已发出启动指令，等它输出 Done 才算真正就绪"}


@tool(perm=("server", "control"), mutates=True)
async def server_stop(server_id: int) -> dict[str, Any]:
    """正常停止服务器（RCON stop -> stdin -> SIGTERM 逐级降级，会保存世界）。"""
    async with session_scope() as s:
        row = await _server(s, server_id)
        if _snap(server_id).get("state") == "stopped":
            return {"state": "stopped", "note": "服务器本来就停着"}
        await manager.stop(row)
        return {"state": "stopping", "note": "已发出停止指令，等待进程退出"}


@tool(perm=("server", "control"), mutates=True)
async def server_restart(server_id: int) -> dict[str, Any]:
    """重启服务器：先正常停止再启动。改完配置后用这个让配置生效。"""
    async with session_scope() as s:
        row = await _server(s, server_id)
        await manager.restart(row)
        return {"state": _snap(server_id).get("state"), "note": "已重启"}


@tool(perm=("server", "control"), dangerous=True, mutates=True)
async def server_kill(server_id: int) -> dict[str, Any]:
    """强制结束服务器进程。只在卡死、正常停止无效时用——**可能丢失未保存的世界数据**。"""
    async with session_scope() as s:
        row = await _server(s, server_id)
        await manager.stop(row, force=True)
        return {"state": _snap(server_id).get("state"), "note": "已强制结束进程"}


@tool(perm=("server", "console"), mutates=True)
async def send_console(server_id: int, command: str) -> dict[str, Any]:
    """向服务器控制台发送一条命令并返回输出（走 RCON）。例如 list、say hi、whitelist add Steve、gamemode creative Steve。"""
    command = (command or "").strip()
    if not command:
        raise ToolError("命令不能为空")
    async with session_scope() as s:
        row = await _server(s, server_id)
        if _snap(server_id).get("state") != "running":
            raise ToolError("服务器没在运行，控制台命令发不进去")
        output = await manager.send(row, command)
    return {"command": command, "output": _clip(output or "(无输出)", 4000)}


@tool(perm=("server", "console"), mutates=True)
async def broadcast_announcement(server_id: int, message: str) -> dict[str, Any]:
    """向所有在线玩家广播一条公告（say）。换行会被压成空格，一次只发一条。"""
    async with session_scope() as s:
        row = await _server(s, server_id)
        try:
            clean, output = await ops.broadcast(row, message)
        except (ManagerError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
    return {"message": clean, "output": _clip(output or "(无输出)", 1000)}


@tool(perm=("server", "console"), mutates=True)
async def save_world(server_id: int) -> dict[str, Any]:
    """让服务端立刻把世界写盘（save-all）。批量改方块、重启前、备份前用。"""
    async with session_scope() as s:
        row = await _server(s, server_id)
        try:
            output = await ops.save(row)
        except ManagerError as exc:
            raise ToolError(str(exc)) from exc
    return {"saved": True, "output": _clip(output or "(无输出)", 1000)}


@tool(perm=("server", "console"), dangerous=True, mutates=True)
async def reload_server(server_id: int) -> dict[str, Any]:
    """热重载部分插件/配置（reload）。很多插件不吃 reload，容易留下脏状态——能用重启就别用这个。"""
    async with session_scope() as s:
        row = await _server(s, server_id)
        try:
            output = await ops.reload(row)
        except ManagerError as exc:
            raise ToolError(str(exc)) from exc
    return {"reloaded": True, "output": _clip(output or "(无输出)", 2000)}


@tool(perm=("server", "console"), dangerous=True, mutates=True)
async def set_maintenance_mode(server_id: int, enabled: bool, kick_outsiders: bool = True) -> dict[str, Any]:
    """开/关维护模式。开：save-all + 白名单准入（重启后仍生效）+ 踢出不在白名单也非 OP 的在线玩家；关：解除准入限制。开启会打断玩家游戏，所以是危险操作。"""
    async with session_scope() as s:
        row = await _server(s, server_id)
        try:
            result = await ops.set_maintenance(row, bool(enabled), kick_outsiders=bool(kick_outsiders))
        except ManagerError as exc:
            raise ToolError(str(exc)) from exc
    return {
        "enabled": result["enabled"],
        "kicked": result["kicked"],
        "note": "白名单为空时开启维护会把所有玩家踢下线",
    }


@tool(perm=("server", "read"))
async def read_logs(server_id: int, lines: int = 80, grep: str = "") -> dict[str, Any]:
    """读服务器最近的控制台输出。grep 为关键字过滤（不区分大小写），例如 ERROR、WARN。"""
    lines = max(1, min(int(lines or 80), 400))
    async with session_scope() as s:
        await _server(s, server_id)
    snapshot = manager.get(server_id).console.snapshot(2000)
    rows = [item.get("line", "") for item in snapshot]
    if grep:
        needle = grep.lower()
        rows = [line for line in rows if needle in line.lower()]
    rows = rows[-lines:]
    return {"lines": rows, "count": len(rows), "grep": grep}


# ==========================================================================
# 文件
# ==========================================================================
@tool(perm=("server", "files"))
async def list_files(server_id: int, path: str = "") -> dict[str, Any]:
    """列服务器目录下的文件。path 是相对服务器根目录的路径，空串表示根目录。"""
    async with session_scope() as s:
        row = await _server(s, server_id)
        listing = file_service.listing(row.directory, path)
    return {"path": path or "/", "entries": listing.get("entries", [])}


@tool(perm=("server", "files"))
async def read_file(server_id: int, path: str) -> dict[str, Any]:
    """读服务器目录里的文本文件（最多 64KB）。看配置、脚本、日志都行。"""
    async with session_scope() as s:
        row = await _server(s, server_id)
        target = file_service.resolve(row.directory, path)
        if not target.exists():
            raise ToolError(f"文件不存在：{path}")
        if target.stat().st_size > 5 * 1024 * 1024:
            raise ToolError("文件太大（>5MB），用 list_files 确认后在面板里下载")
        text = await file_service.read_text(target)
    return {"path": path, "content": _clip(text), "bytes": len(text)}


@tool(perm=("server", "files"), dangerous=True, mutates=True)
async def write_file(server_id: int, path: str, content: str, append: bool = False) -> dict[str, Any]:
    """写服务器目录里的文本文件。append=false 会**整体覆盖**原文件（覆盖前自动留一份备份）。改配置优先用 update_config。"""
    async with session_scope() as s:
        row = await _server(s, server_id)
        target = file_service.resolve(row.directory, path)
        if append and target.exists():
            content = await file_service.read_text(target) + content
        await file_service.write_text(target, content)
        size = target.stat().st_size
    return {"path": path, "bytes": size, "mode": "append" if append else "overwrite"}


# ==========================================================================
# 配置
# ==========================================================================
@tool(perm=("server", "config"))
async def read_config(server_id: int, path: str = "server.properties") -> dict[str, Any]:
    """读配置文件并解析成结构化键值（支持 properties/yaml/toml/json）。默认读 server.properties。"""
    async with session_scope() as s:
        row = await _server(s, server_id)
        target = file_service.resolve(row.directory, path)
        doc = config_service.read_document(target)
    text = repr(doc.get("data"))
    if len(text) > MAX_CHARS:
        raise ToolError("配置内容太大，请用 read_file 分段查看")
    return {"path": path, "format": doc.get("format"), "data": doc.get("data")}


@tool(perm=("server", "config"), dangerous=True, mutates=True)
async def update_config(server_id: int, values: dict[str, Any], path: str = "server.properties") -> dict[str, Any]:
    """增量修改配置文件：只传要改的键，其余保持不变。写入前会做语法校验并自动备份。改完通常要重启才生效。"""
    if not isinstance(values, dict) or not values:
        raise ToolError("values 必须是非空对象，例如 {\"difficulty\": \"hard\"}")
    async with session_scope() as s:
        row = await _server(s, server_id)
        target = file_service.resolve(row.directory, path)
        doc = config_service.read_document(target)
        merged = dict(doc.get("data") or {})
        merged.update(values)
        if doc.get("format") == "json":
            # json 分支是整体替换，必须把合并后的完整文档传进去。
            result = config_service.write_document(target, merged, row.directory)
        else:
            result = config_service.write_document(target, values, row.directory)
    return {"path": path, "changed": list(values), "data": result.get("data")}


# ==========================================================================
# 插件 / 数据包
# ==========================================================================
@tool(perm=("server", "plugins"))
async def search_projects(query: str, server_id: int | None = None, source: str = "modrinth", limit: int = 8) -> dict[str, Any]:
    """在插件市场搜索插件/数据包。传 server_id 可按该服务器的类型过滤（paper 只出插件，原版只出数据包）。"""
    limit = max(1, min(int(limit or 8), 20))
    async with session_scope() as s:
        server = await _server(s, server_id) if server_id else None
    hits = await plugin_service.search(query, server=server, source=source, limit=limit)
    return {
        "source": source,
        "results": [
            {
                "id": item.get("id"),
                "slug": item.get("slug"),
                "title": item.get("title"),
                "downloads": item.get("downloads"),
                "description": (item.get("description") or "")[:160],
            }
            for item in hits
        ],
    }


@tool(perm=("server", "plugins"), mutates=True)
async def install_project(server_id: int, project: str, version_id: str = "") -> dict[str, Any]:
    """安装插件/数据包到服务器（自动挑兼容当前 MC 版本的最新版）。project 用 search_projects 给的 slug。"""
    async with session_scope() as s:
        row = await _server(s, server_id)
        result = await plugin_service.install(row, project, version_id=version_id)
    return {"installed": result}


@tool(perm=("server", "plugins"))
async def list_projects(server_id: int) -> dict[str, Any]:
    """列出服务器里已安装的插件/数据包及其启用状态。"""
    async with session_scope() as s:
        row = await _server(s, server_id)
        items = plugin_service.installed(row)
    return {"projects": items, "count": len(items)}


@tool(perm=("server", "plugins"), mutates=True)
async def toggle_project(server_id: int, filename: str, enabled: bool) -> dict[str, Any]:
    """启用或禁用一个插件/数据包（改 .jar 后缀，重启后生效）。"""
    async with session_scope() as s:
        row = await _server(s, server_id)
        result = plugin_service.toggle(row, filename, enabled)
    return {"result": result, "enabled": enabled}


@tool(perm=("server", "plugins"), dangerous=True, mutates=True)
async def remove_project(server_id: int, filename: str) -> dict[str, Any]:
    """删除服务器里的插件/数据包文件。不可撤销。"""
    async with session_scope() as s:
        row = await _server(s, server_id)
        plugin_service.remove(row, filename)
    return {"removed": filename}


# ==========================================================================
# 备份
# ==========================================================================
@tool(perm=("server", "backup"))
async def list_backups(server_id: int) -> dict[str, Any]:
    """列出该服务器已有的整目录备份。"""
    async with session_scope() as s:
        row = await _server(s, server_id)
        items = backup_service.list_archives(row)
    return {"backups": [item.public() for item in items], "count": len(items)}


@tool(perm=("server", "backup"), mutates=True)
async def create_backup(server_id: int, scope: str = "world", label: str = "") -> dict[str, Any]:
    """立即创建备份。scope=world 只备份世界，scope=all 备份整个服务器目录（含插件配置）。"""
    if scope not in {"world", "all"}:
        raise ToolError("scope 只能是 world 或 all")
    async with session_scope() as s:
        row = await _server(s, server_id)
        meta = backup_service.create(row, scope=scope, label=label or "ai")
    return {"backup": meta.public()}


@tool(perm=("server", "backup"), dangerous=True, mutates=True)
async def restore_backup(server_id: int, name: str) -> dict[str, Any]:
    """用备份覆盖当前世界/服务器文件。原文件会被挪到 pre-restore 目录，但**当前进度会丢失**。"""
    async with session_scope() as s:
        row = await _server(s, server_id)
        running = _snap(server_id).get("state") == "running"
        if running:
            raise ToolError("请先停止服务器再恢复备份")
        result = backup_service.restore(row, name)
    return {"restore": result}


# ==========================================================================
# 世界
# ==========================================================================
@tool(perm=("server", "config"))
async def list_worlds(server_id: int) -> dict[str, Any]:
    """列出服务器里的世界（含当前生效的那个、各维度、占用大小）。"""
    async with session_scope() as s:
        row = await _server(s, server_id)
        items = world_service.list_worlds(row)
    return {"worlds": items, "count": len(items)}


@tool(perm=("server", "config"))
async def world_settings(server_id: int) -> dict[str, Any]:
    """读世界玩法设置：难度、模式、种子、视野距离、正版验证、白名单等。"""
    async with session_scope() as s:
        row = await _server(s, server_id)
        data = world_service.settings(row)
    return {"settings": data}


@tool(perm=("server", "config"), mutates=True)
async def update_world_settings(server_id: int, values: dict[str, Any]) -> dict[str, Any]:
    """改世界玩法设置（只传要改的键）。会做类型与范围校验。服务器运行中不允许改。"""
    if not isinstance(values, dict) or not values:
        raise ToolError("values 必须是非空对象")
    async with session_scope() as s:
        row = await _server(s, server_id)
        if _snap(server_id).get("state") == "running":
            raise ToolError("请先停止服务器再改世界设置")
        result = world_service.update_settings(row, values)
    return {"settings": result}


@tool(perm=("server", "config"), mutates=True)
async def create_world(server_id: int, name: str, seed: str = "", level_type: str = "minecraft:normal") -> dict[str, Any]:
    """新建一个世界（写入配置，下次启动生成）。level_type 例如 minecraft:normal / minecraft:flat / minecraft:amplified。"""
    async with session_scope() as s:
        row = await _server(s, server_id)
        if _snap(server_id).get("state") == "running":
            raise ToolError("请先停止服务器再建世界")
        result = world_service.create(row, name, seed=seed, level_type=level_type)
    return {"world": result}


@tool(perm=("server", "config"), mutates=True)
async def activate_world(server_id: int, name: str) -> dict[str, Any]:
    """切换当前使用的世界（改 level-name）。服务器必须停机。"""
    async with session_scope() as s:
        row = await _server(s, server_id)
        if _snap(server_id).get("state") == "running":
            raise ToolError("请先停止服务器再切换世界")
        result = world_service.activate(row, name)
    return {"world": result}


@tool(perm=("server", "config"), dangerous=True, mutates=True)
async def delete_world(server_id: int, name: str) -> dict[str, Any]:
    """删除一个世界。文件会移入回收站目录，但**不可从面板恢复**；当前生效的世界不允许删。"""
    async with session_scope() as s:
        row = await _server(s, server_id)
        if _snap(server_id).get("state") == "running":
            raise ToolError("请先停止服务器再删世界")
        result = world_service.delete(row, name)
    return {"world": result}


# ==========================================================================
# 玩家
# ==========================================================================
@tool(perm=("server", "players"))
async def list_players(server_id: int) -> dict[str, Any]:
    """列出白名单、OP、封禁名单，以及当前在线的玩家。"""
    async with session_scope() as s:
        row = await _server(s, server_id)
        data = player_service.lists(row)
    snap = _snap(server_id)
    return {"lists": data, "online": sorted(snap.get("players") or [])}


@tool(perm=("server", "players"), mutates=True)
async def player_action(server_id: int, action: str, name: str) -> dict[str, Any]:
    """玩家管理。action 取值：op / deop / whitelist_add / whitelist_remove / ban / unban。"""
    act = (action or "").strip().lower()
    handlers: dict[str, Callable[[Server, dict], Any]] = {
        "op": lambda server, profile: player_service.op_add(server, profile),
        "deop": lambda server, profile: player_service.op_remove(server, name),
        "whitelist_add": lambda server, profile: player_service.whitelist_add(server, profile),
        "whitelist_remove": lambda server, profile: player_service.whitelist_remove(server, name),
        "ban": lambda server, profile: player_service.ban_add(server, profile),
        "unban": lambda server, profile: player_service.ban_remove(server, name),
    }
    handler = handlers.get(act)
    if handler is None:
        raise ToolError(f"不支持的 action：{action}，可选 {'/'.join(handlers)}")
    async with session_scope() as s:
        row = await _server(s, server_id)
        profile = await player_service.resolve(row, name)
        result = handler(row, profile)
    return {"action": act, "player": name, "result": result}


@tool(perm=("server", "players"))
async def player_stats(server_id: int, limit: int = 15) -> dict[str, Any]:
    """玩家游戏统计：在线时长、死亡次数、击杀数、最后在线时间（取自 world/stats）。"""
    limit = max(1, min(int(limit or 15), 100))
    async with session_scope() as s:
        row = await _server(s, server_id)
        rows = player_service.statistics(row, limit=limit)
    return {"players": rows, "count": len(rows)}


# ==========================================================================
# 任务 / 系统
# ==========================================================================
@tool(perm=("server", "tasks"))
async def list_tasks() -> dict[str, Any]:
    """列出定时任务（备份、重启等）及其下次执行时间。"""
    async with session_scope() as s:
        rows = (await s.execute(select(ScheduledTask).order_by(ScheduledTask.id))).scalars().all()
        items = [
            {
                "id": row.id,
                "name": row.name,
                "kind": row.kind,
                "cron": row.cron,
                "enabled": row.enabled,
                "last_status": row.last_status,
                "next_run": task_service.next_run(row.id),
            }
            for row in rows
        ]
    return {"tasks": items, "count": len(items)}


@tool(perm=("server", "tasks"), mutates=True)
async def run_task(task_id: int) -> dict[str, Any]:
    """立刻执行一个定时任务（不等 cron）。"""
    result = await task_service.run_task(task_id, source="ai")
    return {"task_id": task_id, "result": result}


@tool(perm=("server", "read"))
async def panel_info() -> dict[str, Any]:
    """面板总体信息：对外地址、MCP 模式、服务器类型、Java 运行时、数据目录、时间区。诊断环境与回答“怎么连服”先看这个。"""
    from .. import addresses as addr

    runtimes = []
    for item in await java_service.detect():
        runtimes.append({"major": item.major, "version": item.version, "vendor": item.vendor, "path": str(item.path)})
    kinds = [{"kind": key, "label": key} for key in provision.KINDS]
    async with session_scope() as s:
        address_cfg = await addr.load(s)
    return {
        "kinds": kinds,
        "java": runtimes,
        "data_dir": str(settings.data_dir.resolve()),
        "timezone": settings.timezone,
        "servers_dir": str(settings.servers_dir),
        "panel_url": address_cfg.panel_url or "（未配置，按访问 Host 推断）",
        "mcp_mode": address_cfg.mcp_mode,
        "mcp_address": f"{address_cfg.mcp_host}:{address_cfg.mcp_port}",
    }


# ==========================================================================
# Agent 自身能力：任务清单 + 运维手册
# ==========================================================================
@tool(perm=("server", "read"))
async def list_playbooks() -> dict[str, Any]:
    """列出内置的运维手册名称与一句话摘要。不确定多步任务的标准流程时先看这里，再用 load_playbook 取细节。"""
    return {"playbooks": playbooks.catalogue(), "count": len(playbooks.PLAYBOOKS)}


@tool(perm=("server", "read"))
async def load_playbook(name: str) -> dict[str, Any]:
    """按名称读取一份运维手册的完整步骤。name 用 list_playbooks 返回的名称，例如 install_plugin。"""
    item = playbooks.get(name)
    if item is None:
        raise ToolError(f"没有名为 {name} 的手册，先用 list_playbooks 查名称")
    return item


@tool(perm=("server", "read"))
async def update_plan(title: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
    """把当前任务拆成清单并标出进度。多步操作（安装、备份恢复、排查启动失败）开始时调用一次，每完成一步再更新一次。steps 是 [{"text": "步骤", "status": "pending|doing|done|blocked"}] 这样的数组。"""
    if not steps:
        raise ToolError("steps 不能为空；单步任务不需要清单")
    try:
        parsed = [StepIn.model_validate(item) for item in steps]
    except Exception as exc:
        raise ToolError(f"steps 格式不对：{exc}") from exc
    ctx = current_ctx()
    key = f"{ctx.username}:{ctx.conversation_id or 'default'}"
    plan = plan_service.plans.set(key, title, parsed)
    return {"plan": plan}
