"""AI 助手的 HTTP 接口。

对话走 SSE 而不是 WebSocket：一轮问答本来就是单向流，POST 一个请求拿一条
事件流最省事，也免去连接复用与心跳的麻烦（控制台的实时日志仍然用 WS）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..ai import approvals as approval_store
from ..ai import config as ai_config
from ..ai import plan as plan_store
from ..ai import runs as run_store
from ..ai import tools as ai_tools
from ..ai import trace as trace_store
from ..ai.session import conversations
from ..audit import record
from ..deps import SessionDep, UserDep, client_ip, require
from ..models import User

log = logging.getLogger("nautilus.api.ai")
router = APIRouter(prefix="/ai", tags=["ai"])


class ConfigIn(BaseModel):
    enabled: bool | None = None
    base_url: str | None = Field(default=None, max_length=300)
    model: str | None = Field(default=None, max_length=120)
    #: 传空串表示“不改”（沿用库里的密文），传 null 也一样。
    api_key: str | None = Field(default=None, max_length=400)
    max_steps: int | None = Field(default=None, ge=1, le=40)
    auto_approve: bool | None = None


class ModelsIn(BaseModel):
    #: 允许用界面上刚改、还没保存的地址直接探测；不传则沿用已保存配置。
    base_url: str | None = Field(default=None, max_length=300)
    #: null = 沿用已保存密钥；空串 = 不用密钥（本地 Ollama）。
    api_key: str | None = Field(default=None, max_length=400)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = Field(default=None, max_length=32)


class ApproveIn(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=32)
    approvals: dict[str, bool]


class CancelIn(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=32)


class RenameIn(BaseModel):
    title: str = Field(min_length=1, max_length=28)


class CompactIn(BaseModel):
    #: 用户可指定压缩重点，Claude Code 的 `/compact <focus>` 模式。
    focus: str = Field(default="", max_length=200)


def _key(user: User, conversation_id: str) -> str:
    """会话按用户隔离：id 前缀就是用户 id，别人的 id 拼不出你的 key。"""
    return f"{user.id}:{conversation_id}"


def _sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def _response(events: AsyncIterator[dict[str, Any]]) -> StreamingResponse:
    async def gen() -> AsyncIterator[bytes]:
        try:
            async for event in events:
                yield _sse(event)
        except asyncio.CancelledError:  # 客户端点了停止/关了页面
            raise
        except Exception as exc:
            log.exception("AI 事件流中断")
            yield _sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # 反代（nginx）默认会缓冲整个响应，SSE 必须关掉。
            "X-Accel-Buffering": "no",
        },
    )


# --------------------------------------------------------------------- config
@router.get("/config", dependencies=[Depends(require("system", "read"))])
async def get_config(session: SessionDep) -> dict[str, Any]:
    cfg = await ai_config.load(session)
    return ai_config.public(cfg)


@router.put("/config", dependencies=[Depends(require("system", "manage"))])
async def put_config(
    payload: ConfigIn,
    session: SessionDep,
    request: Request,
    actor: UserDep,
) -> dict[str, Any]:
    cfg = await ai_config.load(session)
    data = payload.model_dump(exclude_none=True)
    if "api_key" in data and data["api_key"] == "":
        data.pop("api_key")  # 空串 = 不动已存的 key
    for field, value in data.items():
        setattr(cfg, field, value)
    await ai_config.save(session, cfg)
    await record(
        session,
        action="ai.config",
        actor=actor,
        target=cfg.model,
        detail={"model": cfg.model, "base_url": cfg.base_url, "enabled": cfg.enabled, "auto_approve": cfg.auto_approve},
        ip=client_ip(request),
    )
    return ai_config.public(await ai_config.load(session))


@router.post("/config/test", dependencies=[Depends(require("system", "manage"))])
async def test_config(session: SessionDep) -> dict[str, Any]:
    """拿当前配置发一句 ping，验证端点/密钥/模型名是不是真的能用。"""
    cfg = await ai_config.load(session)
    try:
        model = ai_config.build_model(cfg)
    except ai_config.AiConfigError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    from pydantic_ai import Agent

    try:
        agent = Agent(model, instructions="只回复 OK 两个字符，不要别的。")
        result = await asyncio.wait_for(agent.run("ping"), timeout=30)
    except TimeoutError:
        return {"ok": False, "error": "连接超时（30s）"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "reply": str(result.output)[:80], "model": cfg.model}


@router.post("/models", dependencies=[Depends(require("system", "manage"))])
async def list_models(payload: ModelsIn, session: SessionDep) -> dict[str, Any]:
    """直接列出端点上的模型，省得手填模型名。

    支持 OpenAI 兼容的 ``/v1/models`` 与 Ollama 原生的 ``/api/tags``，
    失败原因随响应返回给前端展示，不当成 5xx。
    """
    from ..ai import discovery

    cfg = await ai_config.load(session)
    base_url = payload.base_url if payload.base_url is not None else cfg.base_url
    api_key = cfg.api_key if payload.api_key is None else payload.api_key
    if not base_url.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请先填写模型端点")
    return await discovery.discover(base_url, api_key)


@router.get("/tools", dependencies=[Depends(require("server", "read"))])
async def list_tools() -> dict[str, Any]:
    return {"tools": ai_tools.catalogue(), "count": len(ai_tools.TOOLS)}


# ----------------------------------------------------------------------- chat
@router.post("/chat", dependencies=[Depends(require("server", "read"))])
async def chat(payload: ChatIn, session: SessionDep, request: Request, user: UserDep):
    from ..ai.agent import stream_turn  # 延迟导入：不用 AI 的部署不必付这个启动开销

    cfg = await ai_config.load(session)
    if not cfg.enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "AI 助手未启用")

    conversation_id = payload.conversation_id or conversations.new_id()
    key = _key(user, conversation_id)
    if run_store.runs.active(key):
        # 两份 run 同时写同一个 message_history 会互相覆盖；明确拒绝比静默丢历史好。
        raise HTTPException(status.HTTP_409_CONFLICT, "这一轮还在运行，先点停止再发新消息")
    token = run_store.runs.register(key)
    run_id = trace_store.traces.new_id()
    ctx = ai_tools.Ctx(
        role=user.role,
        user_id=user.id,
        username=user.username,
        ip=client_ip(request),
        source="panel",
        conversation_id=key,
    )

    async def events() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "open", "conversation_id": conversation_id, "run_id": run_id, "model": cfg.model}
        async for event in stream_turn(
            prompt=payload.message,
            conversation_id=key,
            ctx=ctx,
            cfg=cfg,
            cancellation_token=token,
            run_id=run_id,
        ):
            yield event

    return _response(events())


@router.post("/approve", dependencies=[Depends(require("server", "read"))])
async def approve(payload: ApproveIn, session: SessionDep, request: Request, user: UserDep):
    """对“需要确认”的工具调用给出批准/拒绝，并继续这一轮。"""
    from ..ai.agent import stream_turn

    cfg = await ai_config.load(session)
    key = _key(user, payload.conversation_id)
    if conversations.get(key) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在或已过期")
    # 过期页面或重复点击不能再触发一次确认。
    problem = approval_store.approvals.validate(key, payload.approvals)
    if problem:
        raise HTTPException(status.HTTP_409_CONFLICT, problem)
    if run_store.runs.active(key):
        raise HTTPException(status.HTTP_409_CONFLICT, "这一轮还在运行，先点停止再确认")
    approval_store.approvals.consume(key)
    token = run_store.runs.register(key)
    # 审批续跑复用同一条轨迹：把“提问 → 等确认 → 执行”连成一条时间线。
    waiting = trace_store.traces.latest(key, status="waiting_approval")
    run_id = waiting.run_id if waiting else trace_store.traces.new_id()
    ctx = ai_tools.Ctx(
        role=user.role,
        user_id=user.id,
        username=user.username,
        ip=client_ip(request),
        source="panel",
        conversation_id=key,
    )

    async def events() -> AsyncIterator[dict[str, Any]]:
        async for event in stream_turn(
            prompt=None,
            conversation_id=key,
            ctx=ctx,
            cfg=cfg,
            approvals=payload.approvals,
            cancellation_token=token,
            run_id=run_id,
        ):
            yield event

    return _response(events())


@router.post("/cancel", dependencies=[Depends(require("server", "read"))])
async def cancel(payload: CancelIn, user: UserDep) -> dict[str, Any]:
    """停止正在运行的对话。直接取消 agent run，而不只是断开浏览器的流。"""
    stopped = run_store.runs.cancel(_key(user, payload.conversation_id))
    return {"ok": True, "stopped": stopped}


@router.get("/conversations", dependencies=[Depends(require("system", "read"))])
async def list_conversations(user: UserDep) -> dict[str, Any]:
    prefix = f"{user.id}:"
    items = [item for item in conversations.summary() if item["id"].startswith(prefix)]
    for item in items:
        item["id"] = item["id"][len(prefix) :]
    return {"conversations": items}


@router.get(
    "/conversations/{conversation_id}/messages",
    dependencies=[Depends(require("system", "read"))],
)
async def conversation_messages(conversation_id: str, user: UserDep) -> dict[str, Any]:
    """切回旧会话时把历史还原成前端可回放的事件序列。"""
    from ..ai.session import serialize

    key = _key(user, conversation_id)
    messages = conversations.get(key)
    if messages is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在或已过期")
    return {"events": serialize(messages), "meta": conversations.meta(key)}


@router.get(
    "/conversations/{conversation_id}/runs",
    dependencies=[Depends(require("system", "read"))],
)
async def conversation_runs(conversation_id: str, user: UserDep) -> dict[str, Any]:
    """列出某次会话的运行记录摘要（按用户隔离）。"""
    key = _key(user, conversation_id)
    runs = trace_store.traces.list_conversation(key)
    return {"runs": runs, "count": len(runs)}


@router.get("/runs/{run_id}", dependencies=[Depends(require("system", "read"))])
async def run_trace(run_id: str, user: UserDep) -> dict[str, Any]:
    """单次运行的完整步骤轨迹。轨迹 key 带用户前缀，越权访问按不存在处理。"""
    trace = trace_store.traces.get(run_id)
    if trace is None or not trace.conversation_id.startswith(f"{user.id}:"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "运行记录不存在或已过期")
    return {"run": trace_store.traces.export(run_id)}


@router.patch("/conversations/{conversation_id}", dependencies=[Depends(require("system", "read"))])
async def rename_conversation(conversation_id: str, payload: RenameIn, user: UserDep) -> dict[str, Any]:
    key = _key(user, conversation_id)
    if not conversations.set_title(key, payload.title):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在或已过期")
    return {"ok": True, "title": payload.title.strip()}


@router.post(
    "/conversations/{conversation_id}/compact",
    dependencies=[Depends(require("system", "manage"))],
)
async def compact_conversation(
    conversation_id: str,
    payload: CompactIn,
    session: SessionDep,
    user: UserDep,
) -> dict[str, Any]:
    """手动压缩（对齐 `/compact <focus>`），自动压缩在每轮开始时也会做。"""
    from ..ai import compaction

    cfg = await ai_config.load(session)
    key = _key(user, conversation_id)
    messages = conversations.get(key)
    if messages is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在或已过期")
    if run_store.runs.active(key):
        raise HTTPException(status.HTTP_409_CONFLICT, "这一轮还在运行，先点停止再压缩")
    new_messages, info = await compaction.compact(messages, cfg, focus=payload.focus, force=True)
    if not info["compacted"]:
        return {"ok": True, "compacted": False, "reason": "会话太短，无需压缩"}
    old_count = (conversations.meta(key) or {}).get("compactions", 0)
    conversations.put(key, new_messages, summary=info["summary"], compactions=old_count + 1)
    return {"ok": True, **info}


@router.delete("/conversations/{conversation_id}", status_code=204, dependencies=[Depends(require("server", "read"))])
async def drop_conversation(conversation_id: str, user: UserDep) -> None:
    key = _key(user, conversation_id)
    run_store.runs.drop(key)
    approval_store.approvals.drop(key)
    plan_store.plans.drop(key)
    trace_store.traces.drop_conversation(key)
    conversations.drop(key)
