"""把面板暴露成 MCP server，让外部 agent（Claude Desktop / Cursor / 自研客户端）
也能操纵这台机器上的 Minecraft 服务器。

用法：

    python -m nautilus.mcp_server                 # stdio（给本地 MCP 客户端拉起）
    python -m nautilus.mcp_server --http 8765     # streamable-http，给远端 agent

客户端配置示例：

    {
      "mcpServers": {
        "nautilus": {
          "command": "C:/path/to/nautilus-mc/.venv/Scripts/python.exe",
          "args": ["-m", "nautilus.mcp_server"],
          "env": {"NAUTILUS_DATA_DIR": "C:/path/to/nautilus-mc/data"}
        }
      }
    }

跟面板内置对话共用 ``nautilus.ai.tools`` 里的同一批工具 —— 同一份权限模型、
同一套审计，不存在“MCP 这条路径更宽松”的情况。身份由 ``NAUTILUS_MCP_ROLE``
决定（默认 admin，因为能起这个进程的人本来就能直接改文件）。
"""

from __future__ import annotations

import argparse
import inspect
import os
import sys
from contextlib import asynccontextmanager
from functools import wraps
from typing import Any

from mcp.server.mcpserver import MCPServer

from .ai import tools

INSTRUCTIONS = """Nautilus 是 Minecraft 服务器管理面板。这些工具可以直接启停服务器、
读写文件与配置、装插件、做备份、管理世界与玩家。

约定：
- 先 list_servers 拿 server_id。
- 每个工具返回 {"ok": true/false}；失败看 error。
- 不可逆操作（删世界/删插件/覆盖文件/恢复备份）执行前请向用户确认。
"""


def _identity() -> tools.Ctx:
    return tools.Ctx(
        role=os.environ.get("NAUTILUS_MCP_ROLE", "admin"),
        username=os.environ.get("NAUTILUS_MCP_USER", "mcp"),
        ip="mcp",
        source="mcp",
    )


def _bind(spec: tools.ToolSpec) -> Any:
    """把工具包一层：每次调用都带上 MCP 的身份，顺便保住原签名。"""

    @wraps(spec.fn)
    async def bound(*args: Any, **kwargs: Any) -> Any:
        token = tools.set_ctx(_identity())
        try:
            return await spec.fn(*args, **kwargs)
        finally:
            tools.reset_ctx(token)

    bound.__signature__ = inspect.signature(spec.fn)  # type: ignore[attr-defined]
    return bound


@asynccontextmanager
async def _lifespan(_server: MCPServer):
    """MCP 进程自己也要跑迁移 —— 它可能先于面板被启动。"""
    from .db import init_db

    await init_db()
    yield {}


def build_server() -> MCPServer:
    server = MCPServer(
        name="nautilus",
        title="Nautilus Minecraft Panel",
        instructions=INSTRUCTIONS,
        lifespan=_lifespan,
    )
    for spec in tools.TOOLS.values():
        server.add_tool(
            _bind(spec),
            name=spec.name,
            description=(inspect.getdoc(spec.fn) or spec.summary).strip(),
        )
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nautilus.mcp_server", description="Nautilus MCP server")
    parser.add_argument("--http", type=int, metavar="PORT", help="用 streamable-http 监听该端口（覆盖配置）")
    parser.add_argument("--stdio", action="store_true", help="强制走 stdio（覆盖配置）")
    parser.add_argument("--host", help="HTTP 监听地址；默认读系统设置里的 MCP 地址（初始 127.0.0.1）")
    args = parser.parse_args(argv)

    from .addresses import resolve_for_mcp

    try:
        configured = resolve_for_mcp()
    except Exception as exc:  # noqa: BLE001 - 配置读不到就退回安全默认，不让 MCP 起不来
        print(f"读取地址配置失败（{exc}），使用默认 127.0.0.1:8765", file=sys.stderr)
        configured = None

    host = args.host or (configured.mcp_host if configured else "127.0.0.1")
    port = args.http or (configured.mcp_port if configured else 8765)
    # 命令行 > 配置；配置默认 stdio，只有显式切到 http 才监听端口。
    use_http = bool(args.http) or (not args.stdio and configured is not None and configured.mcp_mode == "http")

    server = build_server()
    if use_http:
        if host not in ("127.0.0.1", "localhost", "::1"):
            print(
                f"警告：MCP 将监听 {host}:{port}，该接口能操纵全部服务器，请确认只在可信网络内暴露。",
                file=sys.stderr,
            )
        # 传参而不是改环境变量：run() 会把 kwargs 透传给 run_streamable_http_async。
        server.run("streamable-http", host=host, port=port)
    else:
        server.run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
