"""AI 助手配置。

存放位置是 ``settings_kv["ai.config"]``（和 webhook 一样，不专门建表）。
**API Key 用 Fernet 加密后落库**：密钥由 ``settings.secret_key`` 派生，
所以即使 ``nautilus.db`` 被拷走，key 也不会明文躺在里面。

对外只暴露掩码，明文仅在进程内用于构造 provider。
"""

from __future__ import annotations

import base64
import hashlib
import logging
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import SettingKV
from ..settings import settings

log = logging.getLogger("nautilus.ai.config")

KEY = "ai.config"
#: 常见端点，前端下拉直接给这些，也允许自己填。
PRESETS: tuple[dict[str, str], ...] = (
    {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    {"label": "阿里云百炼（通义）", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
    {"label": "月之暗面 Kimi", "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k"},
    {"label": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-plus"},
    {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    {"label": "本地 Ollama", "base_url": "http://127.0.0.1:11434/v1", "model": "qwen2.5:7b"},
)


class AiConfigError(RuntimeError):
    """配置不完整或不可用。"""


@dataclass
class AiConfig:
    #: 关掉后 /ai/chat 直接拒绝，前端也不显示助手入口。
    enabled: bool = False
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    #: 仅存在于内存；落库前加密。
    api_key: str = ""
    #: 一次对话里最多几次“模型 -> 工具 -> 模型”的往返。
    max_steps: int = 12
    #: 为 False 时，标记为危险的工具要人工点确认。
    auto_approve: bool = False

    def clamp(self) -> AiConfig:
        self.max_steps = max(1, min(int(self.max_steps or 12), 40))
        self.base_url = (self.base_url or "").strip().rstrip("/")
        self.model = (self.model or "").strip()
        return self


def _fernet() -> Fernet:
    # secret_key 在启动时就已持久化（<data>/.secret），所以派生结果是稳定的。
    digest = hashlib.sha256(f"{settings.secret_key}|nautilus.ai".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt(raw: str) -> str:
    if not raw:
        return ""
    return _fernet().encrypt(raw.encode()).decode()


def _decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        # secret_key 换过了：旧密文再也解不开，当作没配 key，别让整个面板崩。
        log.warning("AI api_key 无法解密（secret_key 可能已更换），请重新填写")
        return ""


#: 这些主机上的服务（Ollama / Ollama in Docker / 本机其它兼容端）不需要密钥。
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"})


def is_local_endpoint(base_url: str) -> bool:
    """是否是本机/同机容器端点。用于放宽无 Key 限制和决定是否探测原生接口。"""
    try:
        host = (urlsplit(base_url).hostname or "").lower().strip("[]")
    except ValueError:
        return False
    return host in LOCAL_HOSTS


def key_required(base_url: str) -> bool:
    """https 远端要密钥；本机或明文 http 端点（如 Ollama）不要。"""
    try:
        return urlsplit(base_url).scheme == "https" and not is_local_endpoint(base_url)
    except ValueError:
        return True


def mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 10:
        return "****"
    return f"{key[:4]}****{key[-4:]}"


async def load(session: AsyncSession) -> AiConfig:
    row = await session.get(SettingKV, KEY)
    data: dict[str, Any] = dict(row.value or {}) if row else {}
    return AiConfig(
        enabled=bool(data.get("enabled", False)),
        base_url=data.get("base_url") or AiConfig.base_url,
        model=data.get("model") or AiConfig.model,
        api_key=_decrypt(data.get("api_key", "")),
        max_steps=int(data.get("max_steps", 12) or 12),
        auto_approve=bool(data.get("auto_approve", False)),
    ).clamp()


async def save(session: AsyncSession, cfg: AiConfig, *, keep_key: bool = True) -> AiConfig:
    """写入配置。

    ``keep_key=True`` 且 ``cfg.api_key`` 为空时沿用库里已有的密文 —— 前端
    回传掩码或空串时不该把 key 抹掉。
    """
    cfg.clamp()
    row = await session.get(SettingKV, KEY)
    stored = dict(row.value or {}) if row else {}
    encrypted = _encrypt(cfg.api_key) if cfg.api_key else (stored.get("api_key", "") if keep_key else "")

    payload = {
        "enabled": cfg.enabled,
        "base_url": cfg.base_url,
        "model": cfg.model,
        "api_key": encrypted,
        "max_steps": cfg.max_steps,
        "auto_approve": cfg.auto_approve,
    }
    if row is None:
        session.add(SettingKV(key=KEY, value=payload))
    else:
        row.value = payload
    await session.commit()
    return cfg


def public(cfg: AiConfig) -> dict[str, Any]:
    """给前端的视图：不含明文 key。"""
    data = asdict(cfg)
    data["api_key"] = ""
    data["api_key_masked"] = mask(cfg.api_key)
    data["has_key"] = bool(cfg.api_key)
    data["configured"] = bool(cfg.model and cfg.base_url and (cfg.api_key or not key_required(cfg.base_url)))
    data["key_required"] = key_required(cfg.base_url)
    data["presets"] = list(PRESETS)
    return data


def build_model(cfg: AiConfig) -> Any:
    """按 OpenAI 兼容协议构造模型。

    国内主流厂商（DeepSeek / 百炼 / Kimi / 智谱）和本地 Ollama 都遵守这套
    协议，所以一个 provider 足够，不需要按厂商写分支。
    """
    if not cfg.base_url:
        raise AiConfigError("未配置模型端点 base_url")
    if not cfg.model:
        raise AiConfigError("未配置模型名")
    # 本地服务（Ollama / LM Studio 等）不校验 key；明文 http 端点也不该填真 key。
    # 其余 https 云服务缺少密钥时直接报清楚，别等到第一次对话才失败。
    if not cfg.api_key and key_required(cfg.base_url):
        raise AiConfigError("未配置 API Key")

    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    provider = OpenAIProvider(base_url=cfg.base_url, api_key=cfg.api_key or "ollama")
    return OpenAIChatModel(cfg.model, provider=provider)
