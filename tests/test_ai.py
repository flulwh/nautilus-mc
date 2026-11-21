"""AI 助手：配置加密、工具权限、对话流、审批流、MCP 暴露。"""

from __future__ import annotations

import json
from typing import Any

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from nautilus.ai import config as ai_config
from nautilus.ai import tools as ai_tools
from nautilus.db import session_scope
from nautilus.models import Server, SettingKV


def _events(body: str) -> list[dict[str, Any]]:
    out = []
    for line in body.splitlines():
        if line.startswith("data: "):
            out.append(json.loads(line[6:]))
    return out


async def _enable_ai(**overrides: Any) -> None:
    async with session_scope() as s:
        cfg = await ai_config.load(s)
        cfg.enabled = True
        cfg.base_url = "https://api.example.com/v1"
        cfg.model = "test-model"
        cfg.api_key = "sk-test-1234567890abcdef"
        for field, value in overrides.items():
            setattr(cfg, field, value)
        await ai_config.save(s, cfg)


# --------------------------------------------------------------------- config
async def test_config_encrypts_key(admin):
    r = await admin.put(
        "/api/ai/config",
        json={"enabled": True, "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "api_key": "sk-secret-abcdef123456"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["has_key"] is True
    assert data["api_key"] == "" and "sk-secret" not in r.text
    assert data["configured"] is True
    assert data["api_key_masked"].endswith("3456")

    # 明文不该出现在库里
    async with session_scope() as s:
        row = await s.get(SettingKV, ai_config.KEY)
        assert row is not None
        assert "sk-secret" not in json.dumps(row.value, ensure_ascii=False)


async def test_config_keeps_key_and_validates(admin):
    await admin.put("/api/ai/config", json={"enabled": True, "model": "m1", "api_key": "sk-keep-me-000111222"})
    r = await admin.put("/api/ai/config", json={"model": "m2", "api_key": ""})
    assert r.json()["has_key"] is True, "空串应当表示不改动已存的 key"
    assert r.json()["model"] == "m2"

    r = await admin.put("/api/ai/config", json={"max_steps": 999})
    assert r.status_code == 422, "越界值要被 schema 挡掉"

    # 恢复成一个干净状态，避免影响后续用例
    await admin.put("/api/ai/config", json={"enabled": False, "max_steps": 12})


async def test_config_requires_admin(client):
    r = await client.get("/api/ai/config")
    assert r.status_code in {401, 403}


# ---------------------------------------------------------------------- tools
async def test_tool_permission_denied():
    token = ai_tools.set_ctx(ai_tools.Ctx(role="viewer", username="viewer"))
    try:
        out = await ai_tools.TOOLS["server_kill"].fn(1)
        assert out["ok"] is False
        assert "权限" in out["error"]
        # 只读工具对 viewer 是放行的（会返回“服务器不存在”这种业务错误）
        out = await ai_tools.TOOLS["list_servers"].fn()
        assert out["ok"] is True
    finally:
        ai_tools.reset_ctx(token)


async def test_tool_file_roundtrip():
    async with session_scope() as s:
        server = Server(name="ai-test-server", kind="paper", version="1.20.4", port=25001, rcon_port=25002)
        s.add(server)
        await s.commit()
        await s.refresh(server)
        server.directory.mkdir(parents=True, exist_ok=True)
        server_id = server.id

    token = ai_tools.set_ctx(ai_tools.Ctx(role="admin", username="admin"))
    try:
        out = await ai_tools.TOOLS["write_file"].fn(server_id, "plugins/test.txt", "hello world")
        assert out["ok"] is True, out
        out = await ai_tools.TOOLS["read_file"].fn(server_id, "plugins/test.txt")
        assert out["ok"] is True and out["content"] == "hello world"
        # 目录穿越必须被文件沙箱挡住
        out = await ai_tools.TOOLS["read_file"].fn(server_id, "../../nautilus.db")
        assert out["ok"] is False
    finally:
        ai_tools.reset_ctx(token)


async def test_tool_unknown_server():
    token = ai_tools.set_ctx(ai_tools.Ctx(role="admin"))
    try:
        out = await ai_tools.TOOLS["server_status"].fn(999999)
        assert out["ok"] is False and "不存在" in out["error"]
    finally:
        ai_tools.reset_ctx(token)


# ----------------------------------------------------------------- chat (SSE)
def _as_model(fn) -> FunctionModel:
    """把「给定消息 -> ModelResponse」的函数包成可流式的模型。

    ``stream_function`` 是必需的：面板走 run_stream_events，FunctionModel 没有
    流式实现就会直接报错。这里把同一份响应拆成增量吐出来，顺便验证前端拼接。
    """

    async def stream(messages, info):
        for part in fn(messages, info).parts:
            if isinstance(part, TextPart):
                text = part.content
                half = len(text) // 2 or len(text)
                for chunk in (text[:half], text[half:]):
                    if chunk:
                        yield chunk
            elif isinstance(part, ToolCallPart):
                yield {0: DeltaToolCall(name=part.tool_name, json_args=json.dumps(part.args), tool_call_id=part.tool_call_id)}

    return FunctionModel(fn, stream_function=stream)


def _text_model(reply: str) -> FunctionModel:
    def fn(messages, info):
        return ModelResponse(parts=[TextPart(reply)])

    return _as_model(fn)


def _tool_then_text(tool: str, args: dict[str, Any], reply: str) -> FunctionModel:
    def fn(messages, info):
        for message in messages:
            for part in getattr(message, "parts", ()):
                if type(part).__name__ == "ToolReturnPart":
                    return ModelResponse(parts=[TextPart(reply)])
        return ModelResponse(parts=[ToolCallPart(tool, args)])

    return _as_model(fn)


async def test_chat_streams_text(admin, monkeypatch):
    await _enable_ai()
    monkeypatch.setattr(ai_config, "build_model", lambda cfg: _text_model("你好，我是运维助手"))
    r = await admin.post("/api/ai/chat", json={"message": "你好"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = _events(r.text)
    assert events[0]["type"] == "open" and events[0]["conversation_id"]
    assert any(e["type"] == "text" for e in events)
    done = [e for e in events if e["type"] == "done"]
    assert done and "运维助手" in done[0]["text"]


async def test_chat_runs_tool(admin, monkeypatch):
    await _enable_ai()
    monkeypatch.setattr(
        ai_config, "build_model", lambda cfg: _tool_then_text("list_servers", {}, "已经查完了")
    )
    r = await admin.post("/api/ai/chat", json={"message": "有几台服务器"})
    events = _events(r.text)
    kinds = [e["type"] for e in events]
    assert "tool_call" in kinds and "tool_result" in kinds
    call = next(e for e in events if e["type"] == "tool_call")
    assert call["name"] == "list_servers"
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["id"] == call["id"]
    assert "servers" in result["preview"]


async def test_chat_requires_enabled(admin):
    async with session_scope() as s:
        cfg = await ai_config.load(s)
        cfg.enabled = False
        await ai_config.save(s, cfg)
    r = await admin.post("/api/ai/chat", json={"message": "hi"})
    assert r.status_code == 400
    assert "未启用" in r.text


# ------------------------------------------------------------------- approvals
async def test_dangerous_tool_needs_approval(admin, monkeypatch):
    await _enable_ai(auto_approve=False)
    monkeypatch.setattr(
        ai_config,
        "build_model",
        lambda cfg: _tool_then_text("server_kill", {"server_id": 999999}, "好的，已取消"),
    )
    r = await admin.post("/api/ai/chat", json={"message": "把服务器杀了"})
    events = _events(r.text)
    approval = [e for e in events if e["type"] == "approval_required"]
    assert approval, f"应当停下来等确认，实际事件：{[e['type'] for e in events]}"
    item = approval[0]["items"][0]
    assert item["name"] == "server_kill"
    assert "强制" in item["summary"] or "结束" in item["summary"]

    done = [e for e in events if e["type"] == "done"][0]
    conversation_id = next(e for e in events if e["type"] == "open")["conversation_id"]
    assert done["pending"], "done 里要告诉前端还有待确认项"

    # 拒绝之后这一轮应当正常收尾，而不是卡住
    r = await admin.post(
        "/api/ai/approve",
        json={"conversation_id": conversation_id, "approvals": {item["id"]: False}},
    )
    assert r.status_code == 200
    after = _events(r.text)
    assert any(e["type"] == "done" for e in after)
    assert "已取消" in [e for e in after if e["type"] == "done"][0]["text"]


async def test_approve_unknown_conversation(admin):
    await _enable_ai()
    r = await admin.post("/api/ai/approve", json={"conversation_id": "nope", "approvals": {}})
    assert r.status_code == 404


# ------------------------------------------------------------------------- MCP
async def test_mcp_exposes_every_tool():
    from nautilus.mcp_server import build_server

    server = build_server()
    exposed = {tool.name for tool in await server.list_tools()}
    assert exposed == set(ai_tools.TOOLS)
    assert len(exposed) >= 30
