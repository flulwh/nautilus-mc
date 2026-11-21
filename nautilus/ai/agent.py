"""把工具挂到 pydantic-ai 的 Agent 上，并把一次 run 拆成前端能消费的事件流。

为什么用 pydantic-ai 而不是自己写 tool-calling 循环：它已经把「模型往返 ->
工具执行 -> 再往返」这个状态机、流式增量、以及 ``requires_approval`` 人工审批
做完了，我们再手写一遍只会漏边界情况。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator

from pydantic_ai import Agent, AgentRunResultEvent, DeferredToolRequests, DeferredToolResults, Tool, UsageLimits
from pydantic_ai.messages import (
    DeferredToolRequestsEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    TextPartDelta,
)

from . import config as ai_config
from . import tools
from .session import conversations

log = logging.getLogger("nautilus.ai.agent")

INSTRUCTIONS = """你是 Nautilus 的运维助手，通过工具操纵 Minecraft 服务器面板。

工作方式：
- 先用 list_servers 拿到 server_id，不要猜 id。
- 每个工具都返回 {"ok": true/false, ...}。ok=false 时读 error 字段再决定下一步，
  不要反复重试同一个失败调用。
- 改配置用 update_config（增量、会自动备份）；改完提醒用户需要重启才生效。
- 「装个插件」：search_projects 找 slug -> install_project 安装 -> 提示重启。
- 涉及玩家（op/封禁/白名单）直接用 player_action，别去改 json 文件。
- 服务器卡死才对 server_kill；正常停止永远用 server_stop（会保存世界）。

安全约束：
- 任何不可逆操作（删世界、删插件、覆盖文件、恢复备份），执行前用一句话说明
  影响范围；如果工具返回“需要确认”，就等用户确认，不要绕过。
- 不要编造执行结果。工具没返回的内容，就说没查到。
- 只回答和面板、Minecraft 服务器运维相关的事；其它话题简短带过。

回答用中文，简洁直接，不要复述工具的原始 JSON。"""


class AiUnavailable(RuntimeError):
    """AI 未启用或配置不完整。"""


def build_agent(cfg: ai_config.AiConfig) -> Agent:
    model = ai_config.build_model(cfg)
    need_approval = not cfg.auto_approve
    # 显式 takes_ctx=False：工具通过 ContextVar 拿身份，签名里没有 RunContext。
    # 让 pydantic-ai 去猜的话，带参数的函数会被误判成“上下文工具”而报错。
    toolset = [
        Tool(
            spec.fn,
            name=spec.name,
            description=(inspect.getdoc(spec.fn) or spec.summary).strip(),
            takes_ctx=False,
            requires_approval=spec.dangerous and need_approval,
        )
        for spec in tools.TOOLS.values()
    ]
    return Agent(
        model,
        # 一旦有工具需要审批，run 的返回值可能是 DeferredToolRequests 而不是文本，
        # 所以输出类型必须同时声明这两种。
        output_type=[str, DeferredToolRequests],
        instructions=INSTRUCTIONS,
        tools=toolset,
    )


@dataclass
class Turn:
    """一次 run 的最终产物。"""

    text: str = ""
    pending: list[dict[str, Any]] | None = None
    usage: dict[str, Any] | None = None


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def _tool_args(value: Any) -> Any:
    """工具参数可能是原始 JSON 字符串（流式拼接的结果），解成对象再给前端。"""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return _jsonable(value)


def _preview(content: Any, limit: int = 600) -> str:
    text = content if isinstance(content, str) else str(content)
    return text if len(text) <= limit else text[:limit] + " ..."


def _result_text(content: Any) -> tuple[str, bool]:
    """把工具返回值整理成「给人看的 JSON」并判断成败。

    工具统一返回 dict，但 pydantic-ai 交给我们的是 Python 对象；直接 str()
    会得到单引号的 repr，前端当 JSON 显示时很别扭。
    """
    if isinstance(content, dict):
        failed = content.get("ok") is False
        try:
            return json.dumps(content, ensure_ascii=False), failed
        except (TypeError, ValueError):
            return str(content), failed
    text = str(content)
    return text, '"ok": false' in text or "'ok': False" in text


async def stream_turn(
    *,
    prompt: str | None,
    conversation_id: str,
    ctx: tools.Ctx,
    cfg: ai_config.AiConfig,
    approvals: dict[str, bool] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """跑一轮对话，逐条吐出事件。

    ``approvals`` 不为 None 时表示这是“用户点了确认之后”的续跑：此时不能再给
    新的用户提问（pydantic-ai 会直接报 unprocessed tool calls），只能带审批结果。
    """
    if not cfg.enabled:
        yield {"type": "error", "message": "AI 助手未启用，先在「系统设置 -> AI 助手」里打开并填好模型"}
        return

    try:
        agent = build_agent(cfg)
    except ai_config.AiConfigError as exc:
        yield {"type": "error", "message": str(exc)}
        return

    history = conversations.get(conversation_id) or []
    kwargs: dict[str, Any] = {
        "message_history": history,
        "usage_limits": UsageLimits(request_limit=max(2, cfg.max_steps)),
    }
    if approvals is not None:
        kwargs["deferred_tool_results"] = DeferredToolResults(approvals=dict(approvals))
        user_prompt = None
    else:
        user_prompt = prompt or ""

    token = tools.set_ctx(ctx)
    final: Turn | None = None
    pending: list[dict[str, Any]] | None = None
    try:
        async with agent.run_stream_events(user_prompt, **kwargs) as events:
            async for event in events:
                if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                    delta = event.delta.content_delta
                    if delta:
                        yield {"type": "text", "delta": delta}
                elif isinstance(event, FunctionToolCallEvent):
                    part = event.part
                    yield {
                        "type": "tool_call",
                        "id": part.tool_call_id,
                        "name": part.tool_name,
                        "args": _tool_args(part.args),
                        "dangerous": bool(tools.TOOLS.get(part.tool_name) and tools.TOOLS[part.tool_name].dangerous),
                    }
                elif isinstance(event, FunctionToolResultEvent):
                    part = event.part
                    text, failed = _result_text(part.content)
                    yield {
                        "type": "tool_result",
                        "id": part.tool_call_id,
                        "name": part.tool_name,
                        "preview": _preview(text),
                        "failed": failed,
                    }
                elif isinstance(event, DeferredToolRequestsEvent):
                    requests = event.requests
                    items = [
                        {
                            "id": call.tool_call_id,
                            "name": call.tool_name,
                            "args": _tool_args(call.args),
                            "summary": (tools.TOOLS[call.tool_name].summary if call.tool_name in tools.TOOLS else ""),
                        }
                        for call in requests.approvals
                    ]
                    if items:
                        pending = items
                        yield {"type": "approval_required", "items": items}
                elif isinstance(event, AgentRunResultEvent):
                    result = event.result
                    output = result.output
                    usage = _jsonable(dict(result.usage.__dict__)) if hasattr(result.usage, "__dict__") else None
                    final = Turn(text=output if isinstance(output, str) else "", usage=usage)
                    conversations.put(conversation_id, result.all_messages())
    except asyncio.CancelledError:  # pragma: no cover - 客户端断开
        raise
    except Exception as exc:  # noqa: BLE001 - 模型/网络错误要变成可读信息
        log.warning("AI 对话失败: %s", exc)
        yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
        return
    finally:
        tools.reset_ctx(token)

    if final is None:
        yield {"type": "error", "message": "模型没有返回结果"}
        return

    yield {"type": "done", "text": final.text, "usage": final.usage, "pending": pending}
