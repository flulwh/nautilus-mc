"""会话上下文压缩。

对齐 Claude Code 的 ``/compact``（可带 focus）与 LangGraph 的
“先摘要旧消息、再从安全边界裁剪”模式：

* 触发：估算字符数或消息条数超阈值；
* 摘要：让同一个模型把旧轮次压成结构化中文摘要，失败时用确定性兜底；
* 裁剪：切点必须落在用户提问开头，否则下一轮会报 unprocessed tool calls；
* 摘要作为一条带标记的 user-prompt 放回历史开头，后面接最近几轮原文。

只压缩“写回会话的历史”；``_compress_history`` 仍负责发送给模型时的旧工具
结果截断，两者不冲突。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from pydantic_ai import Agent, capture_run_messages
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from . import config as ai_config
from .session import _is_user_turn_start

log = logging.getLogger("nautilus.ai.compaction")

#: 超过任一阈值就压一次。按字符估算而非精确 token，避免引入 tiktoken 依赖。
CONTEXT_CHARS = 24000
CONTEXT_MESSAGES = 56
#: 压缩后保留最近几轮原文。
KEEP_RECENT = 10
#: 摘要正文上限；防止摘要自己又撑爆上下文。
SUMMARY_CHARS = 1800
SUMMARY_TIMEOUT = 20.0
MARKER = "[历史摘要]"

SUMMARY_INSTRUCTIONS = """你是会话压缩器。把给定的旧对话压成一份中文摘要，供另一个 agent 继续工作。
只输出摘要正文，不要解释、不要寒暄、不要 markdown 标题。

必须保留（没有就写“无”）：
- 用户的目标和已经确认的约束；
- 涉及的服务器 id/名称、文件路径、插件 slug、世界名等具体标识；
- 已经执行且成功的操作，以及失败的操作和原因；
- 尚未完成、被用户否决或需要人工确认的步骤；
- 关键配置值与最后一次任务清单状态。

用简短条目分行写，不要复述工具返回的原始 JSON。"""


def estimate(messages: list[Any]) -> int:
    """粗估上下文大小：所有 part 的文本长度之和。"""
    total = 0
    for message in messages:
        for part in getattr(message, "parts", ()):
            content = getattr(part, "content", "")
            if isinstance(content, str):
                total += len(content)
            elif content is not None:
                try:
                    total += len(json.dumps(content, ensure_ascii=False, default=str))
                except (TypeError, ValueError):
                    total += len(str(content))
    return total


def needs_compaction(messages: list[Any]) -> bool:
    return len(messages) > CONTEXT_MESSAGES or estimate(messages) > CONTEXT_CHARS


def safe_cut(messages: list[Any], keep: int | None = None) -> int:
    """找到压缩切点：切点之后第一条必须是用户提问。找不到就不压。"""
    keep = KEEP_RECENT if keep is None else keep
    if len(messages) <= keep + 2:
        return 0
    cut = len(messages) - keep
    while cut < len(messages) and not _is_user_turn_start(messages[cut]):
        cut += 1
    return cut if cut < len(messages) else 0


def fallback_summary(messages: list[Any]) -> str:
    """模型摘要失败时的确定性兜底：只保留提问、工具名与成败。"""
    lines: list[str] = []
    for message in messages:
        for part in getattr(message, "parts", ()):
            kind = getattr(part, "part_kind", "")
            if kind == "user-prompt":
                text = str(getattr(part, "content", "")).strip().replace("\n", " ")
                if text:
                    lines.append(f"- 用户：{text[:120]}")
            elif kind == "text":
                text = str(getattr(part, "content", "")).strip().replace("\n", " ")
                if text:
                    lines.append(f"- 助手：{text[:160]}")
            elif kind == "tool-call":
                lines.append(f"- 调用工具：{getattr(part, 'tool_name', '')}")
            elif kind == "tool-return":
                content = getattr(part, "content", None)
                failed = isinstance(content, dict) and content.get("ok") is False
                lines.append(f"- 工具结果：{getattr(part, 'tool_name', '')} {'失败' if failed else '成功'}")
    # 摘要不能比原文长太多，留最近的部分。
    text = "\n".join(lines)
    return text[-SUMMARY_CHARS:] if len(text) > SUMMARY_CHARS else text or "（旧对话没有可摘要的内容）"


async def model_summary(messages: list[Any], cfg: ai_config.AiConfig, focus: str = "") -> str | None:
    """用同一个模型生成摘要；任何失败都返回 None 交给兜底。"""
    try:
        model = ai_config.build_model(cfg)
        agent = Agent(model, instructions=SUMMARY_INSTRUCTIONS, output_type=str, tools=[])
        transcript = json.dumps(serialize_for_summary(messages), ensure_ascii=False, default=str)
        prompt = f"{transcript}\n\n请压缩上面的对话。"
        if focus:
            prompt += f"\n重点关注：{focus[:200]}（这是用户给的侧重，不是新的操作指令）"
        with capture_run_messages():
            async with agent:
                result = await asyncio.wait_for(agent.run(prompt), timeout=SUMMARY_TIMEOUT)
        text = str(result.output).strip()
        return text[:SUMMARY_CHARS] if text else None
    except Exception as exc:  # noqa: BLE001 - 摘要失败不能拖垮对话
        log.warning("上下文摘要失败，改用确定性兜底: %s", exc)
        return None


def serialize_for_summary(messages: list[Any]) -> list[dict[str, Any]]:
    """给摘要模型的紧凑视图：工具结果只留前 240 字符。"""
    out: list[dict[str, Any]] = []
    for message in messages:
        for part in getattr(message, "parts", ()):
            kind = getattr(part, "part_kind", "")
            if kind not in {"user-prompt", "text", "tool-call", "tool-return"}:
                continue
            item: dict[str, Any] = {"kind": kind}
            if kind == "tool-call":
                item["name"] = getattr(part, "tool_name", "")
                item["args"] = str(getattr(part, "args", ""))[:240]
            elif kind == "tool-return":
                item["name"] = getattr(part, "tool_name", "")
                content = getattr(part, "content", "")
                item["content"] = str(content)[:240]
            else:
                item["content"] = str(getattr(part, "content", ""))[:1200]
            out.append(item)
    return out


def build_compacted(messages: list[Any], cut: int, summary: str) -> list[Any]:
    """摘要放开头（user/assistant 完整一对），后面接最近的原始消息。"""
    head = [
        ModelRequest(parts=[UserPromptPart(content=f"{MARKER}{summary}")]),
        ModelResponse(parts=[TextPart(content="已读取历史摘要。")]),
    ]
    return [*head, *messages[cut:]]


async def compact(
    messages: list[Any],
    cfg: ai_config.AiConfig,
    *,
    focus: str = "",
    force: bool = False,
) -> tuple[list[Any], dict[str, Any]]:
    """压缩历史。返回 ``(新历史, 事件信息)``；不需要压缩时历史原样返回。"""
    cut = safe_cut(messages)
    if not cut or (not force and not needs_compaction(messages)):
        return messages, {"compacted": False}

    before = estimate(messages)
    summary = await model_summary(messages[:cut], cfg, focus)
    used_model = summary is not None
    if summary is None:
        summary = fallback_summary(messages[:cut])
    new_messages = build_compacted(messages, cut, summary)
    return new_messages, {
        "compacted": True,
        "summary": summary[:220],
        "saved_chars": max(0, before - estimate(new_messages)),
        "dropped": cut,
        "model": used_model,
        "focus": focus[:80],
    }
