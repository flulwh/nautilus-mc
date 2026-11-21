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

    done = next(e for e in events if e["type"] == "done")
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
    assert "已取消" in next(e for e in after if e["type"] == "done")["text"]


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


# ------------------------------------------------------------- agent extras
async def test_plan_and_playbook_tools():
    from nautilus.ai import plan as plan_store

    token = ai_tools.set_ctx(ai_tools.Ctx(username="planner", conversation_id="c1"))
    try:
        out = await ai_tools.TOOLS["update_plan"].fn(
            "安装插件",
            [{"text": "搜索插件", "status": "done"}, {"text": "安装并重启"}],
        )
        assert out["ok"] is True
        assert out["plan"]["steps"][0]["status"] == "done"
        assert plan_store.plans.get("planner:c1") == out["plan"]

        out = await ai_tools.TOOLS["list_playbooks"].fn()
        assert out["ok"] is True and any(item["name"] == "install_plugin" for item in out["playbooks"])
        out = await ai_tools.TOOLS["load_playbook"].fn("install_plugin")
        assert out["ok"] is True and "search_projects" in out["body"]
        assert (await ai_tools.TOOLS["load_playbook"].fn("不存在的手册"))["ok"] is False
        assert (await ai_tools.TOOLS["update_plan"].fn("x", [{"text": ""}]))["ok"] is False
    finally:
        ai_tools.reset_ctx(token)


async def test_chat_emits_plan_event(admin, monkeypatch):
    await _enable_ai()
    monkeypatch.setattr(
        ai_config,
        "build_model",
        lambda cfg: _tool_then_text(
            "update_plan",
            {"title": "做备份", "steps": [{"text": "停服"}, {"text": "备份"}]},
            "计划列好了",
        ),
    )
    r = await admin.post("/api/ai/chat", json={"message": "列个备份计划"})
    events = _events(r.text)
    plans = [event for event in events if event["type"] == "plan"]
    assert plans, f"应当有 plan 事件，实际：{[e['type'] for e in events]}"
    assert plans[0]["plan"]["title"] == "做备份"
    assert len(plans[0]["plan"]["steps"]) == 2


async def test_approve_rejects_wrong_ids_and_replay(admin, monkeypatch):
    await _enable_ai(auto_approve=False)
    monkeypatch.setattr(
        ai_config,
        "build_model",
        lambda cfg: _tool_then_text("server_kill", {"server_id": 999999}, "已处理"),
    )
    r = await admin.post("/api/ai/chat", json={"message": "强杀服务器"})
    events = _events(r.text)
    item = next(event for event in events if event["type"] == "approval_required")["items"][0]
    cid = next(event for event in events if event["type"] == "open")["conversation_id"]

    bad = await admin.post(
        "/api/ai/approve",
        json={"conversation_id": cid, "approvals": {"made-up-id": True}},
    )
    assert bad.status_code == 409, bad.text

    good = await admin.post(
        "/api/ai/approve",
        json={"conversation_id": cid, "approvals": {item["id"]: True}},
    )
    assert good.status_code == 200, good.text

    replay = await admin.post(
        "/api/ai/approve",
        json={"conversation_id": cid, "approvals": {item["id"]: True}},
    )
    assert replay.status_code == 409, "审批用掉后不能再重放"


async def test_cancel_endpoint_without_run(admin):
    r = await admin.post("/api/ai/cancel", json={"conversation_id": "nothing-running"})
    assert r.status_code == 200 and r.json() == {"ok": True, "stopped": False}


async def test_cancel_stops_agent_run(monkeypatch):
    import asyncio

    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel

    from nautilus.ai import agent as ai_agent
    from nautilus.ai import runs as run_store
    from nautilus.ai.session import conversations

    await _enable_ai()
    started = asyncio.Event()

    def fn(messages, info):
        return ModelResponse(parts=[TextPart("hi")])

    async def stream(messages, info):
        started.set()
        yield "partial"
        await asyncio.sleep(30)
        yield "never"

    monkeypatch.setattr(ai_config, "build_model", lambda cfg: FunctionModel(fn, stream_function=stream))
    async with session_scope() as s:
        cfg = await ai_config.load(s)

    key = "cancel-test-conversation"
    token = run_store.runs.register(key)
    ctx = ai_tools.Ctx(username="admin", conversation_id=key)
    events: list[dict] = []

    async def consume():
        async for event in ai_agent.stream_turn(
            prompt="hi", conversation_id=key, ctx=ctx, cfg=cfg, cancellation_token=token
        ):
            events.append(event)

    task = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), 5)
    await asyncio.sleep(0)
    assert run_store.runs.cancel(key) is True
    await asyncio.wait_for(task, 5)
    assert any(event["type"] == "cancelled" for event in events)
    assert conversations.get(key), "取消时已产生的消息要能留给下一轮"
    assert not run_store.runs.active(key)


def test_history_compression_keeps_recent_and_original():
    from dataclasses import replace as dc_replace

    from pydantic_ai.messages import ModelResponse, ToolReturnPart

    from nautilus.ai.agent import _compress_history

    old = ModelResponse(parts=[ToolReturnPart(tool_name="t", content="x" * 1000, tool_call_id="1")])
    recent = ModelResponse(parts=[ToolReturnPart(tool_name="t", content="y" * 1000, tool_call_id="2")])
    messages = [old] * 13 + [recent]
    out = _compress_history(messages)
    assert len(out) == len(messages)
    assert len(out[0].parts[0].content) < 1000 and "已压缩" in out[0].parts[0].content
    assert out[-1].parts[0].content == "y" * 1000
    assert old.parts[0].content == "x" * 1000, "压缩只影响发给模型的副本"
    assert dc_replace(old, parts=[]).parts == []


def test_agent_applies_tool_timeouts():
    from nautilus.ai import agent as ai_agent

    cfg = ai_config.AiConfig(
        enabled=True,
        base_url="http://127.0.0.1:1/v1",
        model="m",
        api_key="x",
    )
    built = ai_agent.build_agent(cfg)
    registered = built._function_toolset.tools
    assert registered["list_servers"].timeout == 60.0
    assert registered["update_config"].timeout == 120.0
    assert registered["create_backup"].timeout == 300.0


# ------------------------------------------------------------ model discovery
def _mock_client(handler):
    import httpx

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_discover_ollama_merges_native_details():
    from nautilus.ai import discovery

    seen = []

    def handler(request):
        import httpx

        seen.append(request.url.path)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "qwen2.5:7b"}, {"id": "cloud-only"}]})
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "qwen2.5:7b",
                            "size": 4_700_000_000,
                            "modified_at": "2026-11-01T00:00:00Z",
                            "details": {"parameter_size": "7.2B", "quantization_level": "Q4_K_M"},
                        }
                    ]
                },
            )
        return httpx.Response(404, text="no")

    client = _mock_client(handler)
    try:
        out = await discovery.discover("http://127.0.0.1:11434/v1", client=client)
    finally:
        await client.aclose()

    assert out["ok"] is True and out["source"] == "ollama"
    assert out["count"] == 2
    by_id = {item["id"]: item for item in out["models"]}
    assert by_id["qwen2.5:7b"]["parameter_size"] == "7.2B"
    assert by_id["qwen2.5:7b"]["label"] == "qwen2.5:7b · 7.2B · Q4_K_M"
    assert by_id["cloud-only"]["label"] == "cloud-only"
    assert seen == ["/v1/models", "/api/tags"]


async def test_discover_accepts_root_url_and_falls_back():
    from nautilus.ai import discovery

    seen = []

    def handler(request):
        import httpx

        seen.append(request.url.path)
        if request.url.path == "/v1/models":
            return httpx.Response(404, text="missing")
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "llama3.2", "details": {"parameter_size": "3B"}}]})
        return httpx.Response(404, text="no")

    client = _mock_client(handler)
    try:
        out = await discovery.discover("http://127.0.0.1:11434", client=client)
    finally:
        await client.aclose()

    assert out["ok"] is True and out["models"][0]["id"] == "llama3.2"
    assert seen == ["/v1/models", "/api/tags"]


async def test_discover_cloud_skips_native_endpoint():
    from nautilus.ai import discovery

    seen = []

    def handler(request):
        import httpx

        seen.append(request.url.path)
        return httpx.Response(200, json={"data": [{"id": "deepseek-chat"}]})

    client = _mock_client(handler)
    try:
        out = await discovery.discover("https://api.deepseek.com/v1", "sk-x", client=client)
    finally:
        await client.aclose()

    assert out["ok"] is True and out["source"] == "openai-compatible"
    assert seen == ["/v1/models"]


async def test_discover_reports_actionable_error():
    from nautilus.ai import discovery

    def handler(request):
        import httpx

        return httpx.Response(401, text="unauthorized")

    client = _mock_client(handler)
    try:
        out = await discovery.discover("https://api.deepseek.com/v1", client=client)
    finally:
        await client.aclose()

    assert out["ok"] is False and out["models"] == []
    assert "401" in out["error"] and "API Key" in out["hint"]


async def test_discover_rejects_bad_url_and_huge_body():
    from nautilus.ai import discovery

    out = await discovery.discover("ftp://localhost:11434/v1")
    assert out["ok"] is False and "http" in out["error"]

    def handler(request):
        import httpx

        return httpx.Response(200, content=b"x" * (discovery.MAX_BYTES + 1))

    client = _mock_client(handler)
    try:
        out = await discovery.discover("http://127.0.0.1:11434", client=client)
    finally:
        await client.aclose()
    assert out["ok"] is False and "2 MB" in out["error"]


async def test_models_endpoint_uses_unsaved_url_and_requires_manage(admin, client, monkeypatch):
    from nautilus.ai import discovery

    captured = {}

    async def fake(base_url, api_key="", *, client=None):
        captured.update({"base_url": base_url, "api_key": api_key})
        return {"ok": True, "models": [{"id": "m1", "label": "m1"}], "count": 1, "source": "ollama"}

    monkeypatch.setattr(discovery, "discover", fake)
    r = await admin.post(
        "/api/ai/models",
        json={"base_url": "http://127.0.0.1:11434/v1", "api_key": ""},
    )
    assert r.status_code == 200, r.text
    assert captured["base_url"] == "http://127.0.0.1:11434/v1"
    assert captured["api_key"] == ""
    assert r.json()["models"][0]["id"] == "m1"

    # conftest 的 admin/client 是同一个客户端，先登出再验权限。
    await client.post("/api/auth/logout")
    r = await client.post("/api/ai/models", json={"base_url": "http://127.0.0.1:11434/v1"})
    assert r.status_code in {401, 403}


def test_local_endpoint_needs_no_key():
    cfg = ai_config.AiConfig(
        enabled=True, base_url="http://host.docker.internal:11434/v1", model="qwen2.5:7b"
    )
    assert ai_config.build_model(cfg) is not None
    assert ai_config.public(cfg)["configured"] is True
    cfg.base_url = "https://api.deepseek.com/v1"
    try:
        ai_config.build_model(cfg)
    except ai_config.AiConfigError as exc:
        assert "API Key" in str(exc)
    else:
        raise AssertionError("https 远端缺密钥应当报错")
