"""模型发现：从兼容 OpenAI 的端点直接列出可用模型。

Ollama 同时暴露两套接口：

* ``GET /v1/models``（OpenAI 兼容）：只有 ``id``，所有兼容服务都有；
* ``GET /api/tags``（原生）：带参数量、量化等级、体积。

先用通用接口拿名单，本地端点再读原生接口补全详情；两者都失败时给出可操作
的错误。只做 GET，不发任何模型推理请求，也不会把 API Key 写进日志。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from .config import is_local_endpoint

log = logging.getLogger("nautilus.ai.discovery")

#: 探测本机服务要快，连不上不能让设置页卡住。
TIMEOUT = httpx.Timeout(6.0, connect=3.0)
#: 模型名单不该超过 2 MB；防止反代返回一个超大页面把内存吃满。
MAX_BYTES = 2_000_000
UA = "NautilusMC/0.1"


class DiscoveryError(RuntimeError):
    """端点不可达，或者返回的不是预期的 JSON。"""


@dataclass(frozen=True)
class FoundModel:
    id: str
    size: int | None = None
    parameter_size: str = ""
    quantization: str = ""
    modified_at: str = ""

    @property
    def label(self) -> str:
        bits = [self.id]
        if self.parameter_size:
            bits.append(self.parameter_size)
        if self.quantization:
            bits.append(self.quantization)
        return " · ".join(bits)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "size": self.size,
            "parameter_size": self.parameter_size,
            "quantization": self.quantization,
            "modified_at": self.modified_at,
        }


def endpoints(base_url: str) -> tuple[str, str]:
    """返回 ``(OpenAI /v1/models 地址, Ollama 原生根地址)``。

    用户可能填 ``http://127.0.0.1:11434/v1`` 也可能填 ``http://127.0.0.1:11434``，
    两种都要能列出模型。
    """
    base = (base_url or "").strip().rstrip("/")
    if urlsplit(base).scheme not in {"http", "https"}:
        raise DiscoveryError("端点地址必须以 http:// 或 https:// 开头")
    if urlsplit(base).path.rstrip("/").endswith("/v1"):
        return f"{base}/models", base[:-3].rstrip("/")
    return f"{base}/v1/models", base


async def _get_json(client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> Any:
    try:
        async with client.stream("GET", url, headers=headers) as response:
            if response.status_code >= 400:
                raise DiscoveryError(f"HTTP {response.status_code}")
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > MAX_BYTES:
                    raise DiscoveryError("响应超过 2 MB，不像模型列表")
                chunks.append(chunk)
            return json.loads(b"".join(chunks))
    except DiscoveryError:
        raise
    except (httpx.HTTPError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise DiscoveryError(f"{type(exc).__name__}: {exc}") from exc


def _from_openai(payload: Any) -> list[FoundModel]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise DiscoveryError("/v1/models 响应里没有 data 数组")
    out: list[FoundModel] = []
    for item in payload["data"]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("id") or "").strip()
        if name:
            out.append(FoundModel(id=name))
    if not out:
        raise DiscoveryError("端点返回了空的模型列表")
    return out


def _from_ollama(payload: Any) -> list[FoundModel]:
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise DiscoveryError("/api/tags 响应里没有 models 数组")
    out: list[FoundModel] = []
    for item in payload["models"]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("model") or "").strip()
        if not name:
            continue
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        out.append(
            FoundModel(
                id=name,
                size=int(item["size"]) if isinstance(item.get("size"), (int, float)) else None,
                parameter_size=str(details.get("parameter_size") or ""),
                quantization=str(details.get("quantization_level") or ""),
                modified_at=str(item.get("modified_at") or ""),
            )
        )
    return out


def _merge(common: list[FoundModel], native: list[FoundModel]) -> list[FoundModel]:
    by_id = {item.id: item for item in common}
    for item in native:
        old = by_id.get(item.id)
        # 原生接口有体积/量化信息，优先用它；云模型只在 /v1/models 里出现时保留。
        if old is None or (item.parameter_size or item.quantization or item.size):
            by_id[item.id] = item
    return sorted(by_id.values(), key=lambda item: item.id.lower())


async def discover(
    base_url: str,
    api_key: str = "",
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """列出端点上的模型。

    返回 ``{"ok": bool, "models": [...], "count": int, "source": str}``；
    失败时把原因放在 ``error`` 里交给前端展示，不抛给调用方。
    """
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": UA})
    try:
        openai_url, root_url = endpoints(base_url)
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        common: list[FoundModel] = []
        native: list[FoundModel] = []
        openai_error: Exception | None = None
        native_error: Exception | None = None

        try:
            common = _from_openai(await _get_json(client, openai_url, headers))
        except DiscoveryError as exc:
            openai_error = exc
            log.info("模型发现：%s 不可用（%s）", openai_url, exc)

        # 本地端点（Ollama 的典型部署方式）额外读原生接口；/v1/models 已经失败
        # 时也试一次，兼容用户把 base_url 填成根地址的情况。
        if is_local_endpoint(base_url) or openai_error is not None:
            try:
                native = _from_ollama(await _get_json(client, f"{root_url}/api/tags", headers))
            except DiscoveryError as exc:
                native_error = exc
                log.info("模型发现：%s 不可用（%s）", f"{root_url}/api/tags", exc)

        models = _merge(common, native)
        if models:
            return {
                "ok": True,
                "models": [item.public() for item in models],
                "count": len(models),
                "source": "ollama" if native else "openai-compatible",
            }

        detail = openai_error or native_error or DiscoveryError("没有发现可用模型")
        hint = ""
        if "401" in str(detail) or "403" in str(detail):
            hint = "端点要求鉴权，请检查 API Key。"
        elif "404" in str(detail):
            hint = "端点路径不对：填服务根地址或 .../v1 都可以。"
        elif "ConnectError" in str(detail) or "timeout" in str(detail).lower():
            hint = "连不上服务：确认地址可访问，本机 Ollama 默认在 127.0.0.1:11434。"
        return {"ok": False, "models": [], "count": 0, "source": "", "error": str(detail), "hint": hint}
    except DiscoveryError as exc:
        return {"ok": False, "models": [], "count": 0, "source": "", "error": str(exc)}
    finally:
        if own_client:
            await client.aclose()
