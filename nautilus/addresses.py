"""全局地址配置。

存 ``settings_kv["addresses.config"]`` ——和 webhook / AI 配置一样是少量整体读写的
配置，不值得单独建表。覆盖两类地址：

* ``panel_url``：面板对外地址，用于生成可点的链接、API Token 说明、MCP 客户端配置。
  留空时按每次请求的 ``Host`` 头推断，单机本地访问无需配置。
* MCP 监听地址：``mcp_server`` 进程的默认 host/port；命令行参数优先于这里。

**安全默认**：MCP 只监听 127.0.0.1。改成 0.0.0.0 等于把「能操纵全部服务器」的
接口开到局域网，配置接口会给出明确警告，但允许用户在自己可控的内网里这么做。
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .models import SettingKV

log = logging.getLogger("nautilus.addresses")

KEY = "addresses.config"


class AddressError(ValueError):
    """地址格式不合法。"""


@dataclass
class Addresses:
    panel_url: str = ""
    #: stdio = 由 MCP 客户端拉起（默认，最安全）；http = streamable-http 常驻服务。
    mcp_mode: str = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8765

    def clamp(self) -> "Addresses":
        self.panel_url = (self.panel_url or "").strip().rstrip("/")
        if self.panel_url and not self.panel_url.startswith(("http://", "https://")):
            raise AddressError("面板地址必须以 http:// 或 https:// 开头")
        if len(self.panel_url) > 300:
            raise AddressError("面板地址过长（最多 300 字符）")
        self.mcp_mode = (self.mcp_mode or "stdio").strip().lower()
        if self.mcp_mode not in ("stdio", "http"):
            raise AddressError("MCP 模式只能是 stdio 或 http")
        self.mcp_host = (self.mcp_host or "").strip() or "127.0.0.1"
        if len(self.mcp_host) > 120:
            raise AddressError("MCP 监听地址过长")
        try:
            self.mcp_port = int(self.mcp_port)
        except (TypeError, ValueError) as exc:
            raise AddressError("MCP 端口必须是数字") from exc
        if not 1024 <= self.mcp_port <= 65535:
            raise AddressError("MCP 端口必须在 1024~65535 之间")
        return self


async def load(session: AsyncSession) -> Addresses:
    row = await session.get(SettingKV, KEY)
    data: dict[str, Any] = dict(row.value or {}) if row else {}
    return Addresses(
        panel_url=data.get("panel_url", ""),
        mcp_mode=data.get("mcp_mode", "stdio"),
        mcp_host=data.get("mcp_host", "127.0.0.1"),
        mcp_port=int(data.get("mcp_port", 8765) or 8765),
    ).clamp()


async def save(session: AsyncSession, cfg: Addresses) -> Addresses:
    cfg.clamp()
    payload = asdict(cfg)
    row = await session.get(SettingKV, KEY)
    if row is None:
        session.add(SettingKV(key=KEY, value=payload))
    else:
        row.value = payload
    await session.commit()
    return cfg


def public(cfg: Addresses, request: Any = None) -> dict[str, Any]:
    """给前端的视图：加上「实际生效」的地址与暴露警告。"""
    observed = ""
    if request is not None:
        observed = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
        proto = observed or request.url.scheme
        observed = f"{proto}://{request.headers.get('host', request.url.netloc)}"
    return {
        **asdict(cfg),
        "effective_panel_url": cfg.panel_url or observed,
        "inferred": not bool(cfg.panel_url),
        "mcp_exposed": cfg.mcp_host not in ("127.0.0.1", "localhost", "::1"),
        "mcp_client_example": _client_example(cfg),
    }


def _client_example(cfg: Addresses) -> str:
    """MCP 客户端配置片段，用户直接复制即可。"""
    import json
    import os

    python = ".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python"
    if cfg.mcp_mode == "http":
        node = {
            "url": f"http://{cfg.mcp_host}:{cfg.mcp_port}/mcp",
            "transport": "streamable-http",
        }
    else:
        node = {
            "command": python,
            "args": ["-m", "nautilus.mcp_server"],
            "env": {"NAUTILUS_DATA_DIR": "."},
        }
    return json.dumps({"mcpServers": {"nautilus": node}}, ensure_ascii=False, indent=2)


def resolve_for_mcp() -> Addresses:
    """给独立 MCP 进程用的同步读取入口（它没有 FastAPI 的 session）。"""
    import asyncio

    from .db import SessionFactory

    async def _read() -> Addresses:
        async with SessionFactory() as session:
            return await load(session)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_read())
    # 已经有事件循环时（例如被 embed 进面板调用），用新线程跑，避免嵌套 loop。
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(_read())).result()
