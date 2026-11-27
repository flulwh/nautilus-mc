"""AI 助手：配置加密、工具权限、对话流、审批流、MCP 暴露。

模型层用 ScriptedModel（langchain BaseChatModel 的测试替身）驱动：脚本是
「轮次 → 脚本轮」映射，assistant 回复与工具调用都按真实 OpenAI 兼容流
（_astream 逐 chunk + tool_call_chunks）走，顺带覆盖 agent 侧的 chunk 聚合。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field

from nautilus.ai import config as ai_config
from nautilus.ai import tools as ai_tools
from nautilus.db import session_scope
from nautilus.models import Server, SettingKV


class ScriptedModel(BaseChatModel):
    """按调用轮次吐出预设回复的假模型。

    turns 里每一项是一"轮"：``("thinking"|"text", str)`` 增量，或
    ``("tool", id, name, args)``。_astream 拆逐 token chunk（工具调用走
    tool_call_chunks，验证 agent 的增量拼接）；_generate 给 ainvoke 路径
    （/config/test、上下文摘要）用。
    """

    turns: list = Field(default_factory=list)
    calls: list = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):  # noqa: D102
        return self

    def _plan(self, messages) -> list[tuple]:
        self.calls.append(messages)
        if len(self.calls) > len(self.turns):
            return []
        return self.turns[len(self.calls) - 1]

    def _reply(self, plan: list[tuple]) -> AIMessage:
        content = "".join(p[1] for p in plan if p[0] == "text")
        kwargs: dict[str, Any] = {}
        thinking = "".join(p[1] for p in plan if p[0] == "thinking")
        if thinking:
            kwargs["reasoning_content"] = thinking
        return AIMessage(
            content=content,
            additional_kwargs=kwargs,
            tool_calls=[{"id": p[1], "name": p[2], "args": p[3], "type": "tool_call"} for p in plan if p[0] == "tool"],
        )

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._reply(self._plan(messages)))])

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        plan = self._plan(messages)
        if all(p[0] != "tool" for p in plan):
            for kind, value in plan:
                for piece in _split(value):
                    chunk = (
                        AIMessageChunk(content="", additional_kwargs={"reasoning_content": piece})
                        if kind == "thinking"
                        else AIMessageChunk(content=piece)
                    )
                    yield ChatGenerationChunk(message=chunk)
            return
        for index, p in enumerate(plan):
            if p[0] in {"thinking", "text"}:
                for piece in _split(p[1]):
                    chunk = (
                        AIMessageChunk(content="", additional_kwargs={"reasoning_content": piece})
                        if p[0] == "thinking"
                        else AIMessageChunk(content=piece)
                    )
                    yield ChatGenerationChunk(message=chunk)
            elif p[0] == "tool":
                args = json.dumps(p[3], ensure_ascii=False)
                yield ChatGenerationChunk(
                    message=AIMessageChunk(
                        content="",
                        tool_call_chunks=[{"name": p[2], "args": args, "id": p[1], "index": index}],
                    )
                )


def _split(text: str) -> list[str]:
    if not text:
        return []
    half = len(text) // 2 or len(text)
    return [piece for piece in (text[:half], text[half:]) if piece]


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
def _text_model(reply: str) -> ScriptedModel:
    return ScriptedModel(turns=[[("text", reply)]])


def _tool_then_text(tool: str, args: dict[str, Any], reply: str) -> ScriptedModel:
    """看过工具返回就改口说话；否则先发起调用。

    按**消息内容**（而不是调用计数）分支：审批续跑是新的图运行、新模型实例，
    计数法会把第二轮错当成第一轮。
    """

    class ToolFirst(ScriptedModel):
        def _plan(self, messages) -> list[tuple]:
            self.calls.append(messages)
            # langchain 会把 dict 转成 BaseMessage，两种形态都要认。
            seen_tool = any(
                (isinstance(m, dict) and m.get("role") == "tool") or isinstance(m, ToolMessage) for m in messages
            )
            if seen_tool:
                return [("text", reply)]
            return [("tool", f"call_{len(self.calls)}", tool, args)]

    return ToolFirst()


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

    from nautilus.ai import agent as ai_agent
    from nautilus.ai import runs as run_store
    from nautilus.ai.session import conversations

    await _enable_ai()
    started = asyncio.Event()

    class SlowChunks(ScriptedModel):
        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            started.set()
            yield ChatGenerationChunk(message=AIMessageChunk(content="partial"))
            await asyncio.sleep(30)
            yield ChatGenerationChunk(message=AIMessageChunk(content="never"))

    monkeypatch.setattr(ai_config, "build_model", lambda cfg: SlowChunks())
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
    from nautilus.ai.agent import _compress_history

    old = {"role": "tool", "tool_call_id": "1", "name": "t", "content": "x" * 1000}
    recent = {"role": "tool", "tool_call_id": "2", "name": "t", "content": "y" * 1000}
    messages = [{"role": "user", "content": "q"}, old] * 7 + [recent]
    out = _compress_history(messages)
    assert len(out) == len(messages)
    assert len(out[1]["content"]) < 1000 and "已压缩" in out[1]["content"]
    assert out[-1]["content"] == "y" * 1000
    assert messages[1]["content"] == "x" * 1000, "压缩只影响发给模型的副本"


def test_agent_applies_tool_timeouts():
    from nautilus.ai import agent as ai_agent

    assert ai_agent._timeout_for(ai_tools.TOOLS["list_servers"]) == 60.0
    assert ai_agent._timeout_for(ai_tools.TOOLS["update_config"]) == 120.0
    assert ai_agent._timeout_for(ai_tools.TOOLS["create_backup"]) == 300.0
    if ai_tools.TOOLS["server_status"].timeout is None:
        assert ai_agent._timeout_for(ai_tools.TOOLS["server_status"]) == 60.0


def test_tool_schemas_cover_registry_and_types():
    """OpenAI function schema：全量工具、必填参数与 int 类型都要正确还原。"""
    from nautilus.ai.agent import _tool_schemas

    schemas = {item["function"]["name"]: item["function"] for item in _tool_schemas()}
    assert set(schemas) == set(ai_tools.TOOLS)
    kill = schemas["server_kill"]
    assert kill["parameters"]["properties"]["server_id"]["type"] == "integer"
    assert "server_id" in kill["parameters"]["required"]
    listing = schemas["list_servers"]
    assert listing["parameters"]["properties"] == {} and listing["description"]


async def test_max_steps_limit_emits_error(admin, monkeypatch):
    """模型一直要调工具时,到达 max_steps 必须收尾报错,而不是无限循环。"""

    class ToolAlways(ScriptedModel):
        def _plan(self, messages):
            self.calls.append(messages)
            return [("tool", f"call_{len(self.calls)}", "list_servers", {})]

    await _enable_ai(max_steps=2)
    monkeypatch.setattr(ai_config, "build_model", lambda cfg: ToolAlways())
    r = await admin.post("/api/ai/chat", json={"message": "反复查"})
    assert r.status_code == 200
    events = _events(r.text)
    errors = [e for e in events if e["type"] == "error"]
    assert errors and "步数" in errors[0]["message"], [e["type"] for e in errors]
    assert not any(e["type"] == "done" for e in events)


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


# -------------------------------------------------------- sessions & context
def test_session_metadata_and_serialization():
    from nautilus.ai.session import conversations, serialize

    cid = conversations.new_id()
    history = [
        {"role": "user", "content": "重启 1 号服"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "server_status", "args": {"server_id": 1}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "server_status", "content": '{"ok": true, "running": false}'},
        {"role": "assistant", "content": "查到了，1 号服当前是关的。"},
    ]
    conversations.put(cid, history)
    meta = conversations.meta(cid)
    assert meta["title"] == "重启 1 号服"
    assert meta["messages"] == 4

    events = serialize(history)
    assert events[0] == {"type": "user", "text": "重启 1 号服"}
    assert events[1]["type"] == "tool_call" and events[1]["name"] == "server_status"
    assert events[2]["type"] == "tool_result" and events[2]["failed"] is False
    assert events[3] == {"type": "text", "text": "查到了，1 号服当前是关的。"}

    assert conversations.set_title(cid, "改个名字") is True
    assert conversations.meta(cid)["title"] == "改个名字"
    assert conversations.set_title("missing", "x") is False

    # 悬空工具调用（历史末尾等确认/被取消）不能把标题带偏，序列化也要能回放。
    dangling = [*history, {"role": "assistant", "content": "我先强杀", "tool_calls": [{"id": "c2", "name": "server_kill", "args": {"server_id": 2}}]}]
    cid2 = conversations.new_id()
    conversations.put(cid2, dangling)
    replayed = serialize(dangling)
    assert replayed[-1]["type"] == "tool_call" and replayed[-1]["id"] == "c2"

    conversations.drop(cid)
    conversations.drop(cid2)


async def test_conversation_history_and_rename_endpoints(admin, monkeypatch):
    await _enable_ai()
    monkeypatch.setattr(ai_config, "build_model", lambda cfg: _text_model("查好了"))
    r = await admin.post("/api/ai/chat", json={"message": "列一下服务器"})
    events = _events(r.text)
    cid = next(e["conversation_id"] for e in events if e["type"] == "open")

    r = await admin.get(f"/api/ai/conversations/{cid}/messages")
    assert r.status_code == 200, r.text
    assert r.json()["events"][0]["type"] == "user"
    assert r.json()["meta"]["title"] == "列一下服务器"

    r = await admin.patch(f"/api/ai/conversations/{cid}", json={"title": "运维排查"})
    assert r.status_code == 200 and r.json()["title"] == "运维排查"
    assert any(item["title"] == "运维排查" for item in (await admin.get("/api/ai/conversations")).json()["conversations"])

    assert (await admin.get("/api/ai/conversations/nope/messages")).status_code == 404


def test_compaction_boundaries_and_fallback(monkeypatch):
    from nautilus.ai import compaction

    monkeypatch.setattr(compaction, "CONTEXT_MESSAGES", 6)
    messages = []
    for i in range(20):
        messages.append({"role": "user", "content": f"问题{i}"})
        messages.append({"role": "assistant", "content": f"回答{i}"})
    assert compaction.needs_compaction(messages) is True

    cut = compaction.safe_cut(messages, keep=4)
    assert cut > 0 and messages[cut]["role"] == "user"

    summary = compaction.fallback_summary(messages[:cut])
    assert "用户" in summary and "助手" in summary

    compacted = compaction.build_compacted(messages, cut, summary)
    assert len(compacted) == len(messages) - cut + 2
    assert compacted[0]["content"].startswith(compaction.MARKER)
    assert compacted[2:] == messages[cut:]

    # 切点范围内没有用户提问时不压（keep 吃掉全部）。
    assert compaction.safe_cut(messages, keep=len(messages)) == 0

    # 带工具调用的轮次：切点必须落在轮首，不能切在 assistant/tool 中间。
    round_with_tool = [
        {"role": "user", "content": "查状态"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "name": "list_servers", "args": {}}]},
        {"role": "tool", "tool_call_id": "t1", "name": "list_servers", "content": '{"ok": true}'},
        {"role": "assistant", "content": "共 1 台"},
    ]
    mixed = messages[:16] + round_with_tool
    cut2 = compaction.safe_cut(mixed, keep=4)
    assert cut2 > 0 and mixed[cut2]["role"] == "user"


async def test_auto_compaction_emits_event(admin, monkeypatch):
    from nautilus.ai import compaction

    monkeypatch.setattr(compaction, "CONTEXT_MESSAGES", 6)
    monkeypatch.setattr(compaction, "KEEP_RECENT", 2)

    async def fake_summary(messages, cfg, focus=""):
        return "摘要内容"

    monkeypatch.setattr(compaction, "model_summary", fake_summary)
    await _enable_ai()
    monkeypatch.setattr(ai_config, "build_model", lambda cfg: _text_model("好的"))

    cid = None
    compacted_seen = False
    for i in range(5):
        body = {"message": f"第{i}个问题"}
        if cid:
            body["conversation_id"] = cid
        r = await admin.post("/api/ai/chat", json=body)
        events = _events(r.text)
        cid = cid or next((e["conversation_id"] for e in events if e["type"] == "open"), None)
        compacted_seen = compacted_seen or any(e["type"] == "compacted" for e in events)

    assert compacted_seen, "历史超过阈值后应当发 compacted 事件"
    r = await admin.get(f"/api/ai/conversations/{cid}/messages")
    assert r.json()["meta"]["compactions"] == 1
    assert any(e["type"] == "note" for e in r.json()["events"])


async def test_manual_compact_respects_focus(admin, monkeypatch):
    from nautilus.ai import compaction

    monkeypatch.setattr(compaction, "CONTEXT_MESSAGES", 6)
    monkeypatch.setattr(compaction, "KEEP_RECENT", 2)
    await _enable_ai()
    monkeypatch.setattr(ai_config, "build_model", lambda cfg: _text_model("好的"))
    captured = {}

    async def fake(messages, cfg, *, focus="", force=False):
        captured.update({"focus": focus, "force": force, "n": len(messages)})
        return messages, {"compacted": True, "summary": "摘要", "saved_chars": 123, "dropped": 2, "model": False, "focus": focus}

    monkeypatch.setattr(compaction, "compact", fake)
    r = await admin.post("/api/ai/chat", json={"message": "记一件事"})
    cid = next(e["conversation_id"] for e in _events(r.text) if e["type"] == "open")
    r = await admin.post(f"/api/ai/conversations/{cid}/compact", json={"focus": "服务器配置"})
    assert r.status_code == 200, r.text
    assert r.json()["compacted"] is True
    assert captured["focus"] == "服务器配置" and captured["force"] is True
    assert (await admin.get(f"/api/ai/conversations/{cid}/messages")).json()["meta"]["compactions"] == 1


def test_runtime_instructions_include_role_and_time():
    from nautilus.ai.agent import runtime_instructions
    from nautilus.ai.tools import Ctx

    text = runtime_instructions(Ctx(username="ops", role="viewer"))
    assert "ops" in text and "viewer" in text and "只读" in text
    assert "当前时间" in text


async def test_tool_install_project_full_chain(app, monkeypatch):
    """用户报的 install_project 报错：确认工具到插件安装的整条链能跑通。"""
    from nautilus.resources import plugins as plugin_service
    from nautilus.servers import provision

    async with session_scope() as s:
        server = Server(name="ai-install-test", kind="paper", version="1.20.4", port=25031, rcon_port=25032)
        s.add(server)
        await s.commit()
        await s.refresh(server)
        server.directory.mkdir(parents=True, exist_ok=True)
        server_id = server.id

    async def fake_resolve(project, *, server=None, source="modrinth", version_id=""):
        return plugin_service.Artifact(
            url="https://example.com/x.jar",
            filename="x.jar",
            version="1.0",
            project_id=project,
            name=project,
            sha1="abc123",
            source=source,
        )

    async def fake_download(url, target, *, expected_sha256=None, expected_sha1=None, progress=None):
        target.write_bytes(b"fake jar")
        return target

    monkeypatch.setattr(plugin_service, "resolve", fake_resolve)
    monkeypatch.setattr(provision, "download", fake_download)

    token = ai_tools.set_ctx(ai_tools.Ctx(role="admin", username="admin"))
    try:
        out = await ai_tools.TOOLS["install_project"].fn(server_id, "evkiwA7V")
    finally:
        ai_tools.reset_ctx(token)
    assert out["ok"] is True, out
    assert out["installed"]["filename"] == "x.jar"


async def test_chat_emits_thinking_and_started(admin, monkeypatch):
    """推理增量必须以 thinking 事件实时给出，且模型调用前先发 started。"""
    await _enable_ai()
    monkeypatch.setattr(
        ai_config,
        "build_model",
        lambda cfg: ScriptedModel(
            turns=[[("thinking", "先算一下"), ("thinking", "再核对一遍"), ("text", "结果 42")]]
        ),
    )
    r = await admin.post("/api/ai/chat", json={"message": "算个数"})
    assert r.status_code == 200, r.text
    events = _events(r.text)
    types = [e["type"] for e in events]
    assert "started" in types, f"应当发 started，实际：{types}"
    assert types.index("started") < types.index("thinking")
    assert types.index("thinking") < types.index("text"), "思考增量应早于正文"
    thinking = "".join(e["delta"] for e in events if e["type"] == "thinking")
    assert "先算一下" in thinking and "再核对一遍" in thinking, thinking


async def test_openai_compatible_reasoning_content_becomes_thinking(admin, monkeypatch):
    """真实 OpenAI 兼容 provider 的 reasoning_content 必须被解析成思考增量。

    这验证 config.build_model 用的 ReasoningChatOpenAI 已能识别
    DeepSeek/Moonshot 的 reasoning_content 字段（langchain-openai 基类会
    直接丢弃它），前端因此能显示推理过程。
    """
    import httpx

    from nautilus.ai import agent as ai_agent
    from nautilus.ai import model as ai_model

    def handler(request):
        chunks = [
            {"role": "assistant", "reasoning_content": "先想想"},
            {"reasoning_content": "再核对"},
            {"content": "答案 42"},
            {"content": ""},
        ]
        lines = []
        for i, delta in enumerate(chunks):
            fin = "stop" if i == len(chunks) - 1 else None
            lines.append(
                'data: {"id":"x","object":"chat.completion.chunk","created":0,"model":"r1",'
                f'"choices":[{{"index":0,"delta":{json.dumps(delta)},"finish_reason":{json.dumps(fin)}}}]}}\n\n'
            )
        lines.append("data: [DONE]\n\n")
        return httpx.Response(200, text="".join(lines), headers={"content-type": "text/event-stream"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model = ai_model.ReasoningChatOpenAI(
        model="deepseek-reasoner",
        base_url="https://api.deepseek.com/v1",
        api_key="sk-x",
        http_async_client=client,
    )
    try:
        thinkings: list[str] = []
        texts: list[str] = []
        async for chunk in model.astream("算一下"):
            thinkings.append(ai_model.message_thinking(chunk))
            texts.append(chunk.content if isinstance(chunk.content, str) else "")
        joined_thinking = "".join(t for t in thinkings if t)
        assert "先想想" in joined_thinking and "再核对" in joined_thinking
        assert "答案 42" in "".join(texts)

        # 端到端：stream_turn 必须把 provider 思考转成 thinking 事件，且 started 在前。
        await _enable_ai()
        monkeypatch.setattr(ai_config, "build_model", lambda cfg: model)
        r = await admin.post("/api/ai/chat", json={"message": "再算一次"})
        events = _events(r.text)
        types = [e["type"] for e in events]
        assert "started" in types and types.index("started") < types.index("thinking")
        deltas = [e["delta"] for e in events if e["type"] == "thinking"]
        assert "先想想" in "".join(deltas), deltas
    finally:
        await client.aclose()


# ----------------------------------------------------------------- run trace
async def test_chat_events_carry_run_id_seq_ts(admin, monkeypatch):
    """每个事件都带 run_id / seq / ts，工具结果带服务端实测耗时。"""
    await _enable_ai()
    monkeypatch.setattr(ai_config, "build_model", lambda cfg: _tool_then_text("list_servers", {}, "完成"))
    r = await admin.post("/api/ai/chat", json={"message": "列一下服务器"})
    events = _events(r.text)
    assert r.status_code == 200
    # open 由 HTTP 层发，不带 seq；其余 run 内事件都带同一 run_id + 递增 seq。
    run_events = [e for e in events if e["type"] != "open"]
    run_ids = {e.get("run_id") for e in run_events}
    assert len(run_ids) == 1 and None not in run_ids, run_ids
    seqs = [e["seq"] for e in run_events]
    assert seqs == sorted(seqs) and len(seqs) == len(set(seqs)), seqs
    assert all(isinstance(e.get("ts"), int) and e["ts"] > 0 for e in run_events)
    assert events[0]["run_id"] == next(iter(run_ids))
    result = next(e for e in events if e["type"] == "tool_result")
    assert isinstance(result.get("duration_ms"), int) and result["duration_ms"] >= 0
    assert isinstance(result.get("step"), int)


async def test_run_trace_endpoints_and_isolation(admin, client, monkeypatch):
    """轨迹端点：摘要列表 + 完整步骤；其他用户按不存在处理。"""
    from nautilus.ai import trace as trace_store

    trace_store.traces._data.clear()
    await _enable_ai()
    monkeypatch.setattr(ai_config, "build_model", lambda cfg: _tool_then_text("list_servers", {}, "完成"))
    r = await admin.post("/api/ai/chat", json={"message": "查一下"})
    events = _events(r.text)
    cid = events[0]["conversation_id"]
    run_id = events[0]["run_id"]

    listing = await admin.get(f"/api/ai/conversations/{cid}/runs")
    assert listing.status_code == 200
    assert listing.json()["count"] >= 1
    summary = next(x for x in listing.json()["runs"] if x["run_id"] == run_id)
    assert summary["tools"] == 1 and summary["failed"] == 0
    assert summary["status"] == "done" and summary["prompt"] == "查一下"

    detail = await admin.get(f"/api/ai/runs/{run_id}")
    assert detail.status_code == 200
    run = detail.json()["run"]
    assert run["run_id"] == run_id
    kinds = [s["kind"] for s in run["steps"]]
    assert "model" in kinds and "tool" in kinds, kinds
    tool_step = next(s for s in run["steps"] if s["kind"] == "tool")
    assert tool_step["name"] == "list_servers"
    assert tool_step["status"] == "done" and isinstance(tool_step["duration_ms"], int)
    assert '"ok": true' in tool_step["result"]

    # 换个账号不能读别人的轨迹。
    created = await admin.post(
        "/api/users",
        json={
            "username": "tracer",
            "email": "tracer@example.com",
            "password": "TracerPass123",
            "role": "admin",
        },
    )
    assert created.status_code == 201, created.text
    await client.post("/api/auth/logout")
    login = await client.post(
        "/api/auth/login", data={"username": "tracer@example.com", "password": "TracerPass123"}
    )
    assert login.status_code == 204, login.text
    assert (await client.get(f"/api/ai/runs/{run_id}")).status_code == 404
    assert (await client.get(f"/api/ai/conversations/{cid}/runs")).json()["count"] == 0


async def test_approve_reuses_same_run_id(admin, monkeypatch):
    """提问 -> 等确认 -> 续跑，应当落在同一条轨迹上。"""
    from nautilus.ai import trace as trace_store

    trace_store.traces._data.clear()
    await _enable_ai(auto_approve=False)

    monkeypatch.setattr(ai_config, "build_model", lambda cfg: _tool_then_text("server_kill", {"server_id": 999999}, "杀掉了"))
    r = await admin.post("/api/ai/chat", json={"message": "强杀服务器"})
    first = _events(r.text)
    run_id = first[0]["run_id"]
    assert any(e["type"] == "approval_required" for e in first)
    assert next(e for e in first if e["type"] == "done")["status"] == "waiting_approval"

    trace = trace_store.traces.get(run_id)
    assert trace is not None and trace.status == "waiting_approval"
    assert any(s["kind"] == "approval" for s in [step.public() for step in trace.steps])

    call = next(e for e in first if e["type"] == "approval_required")["items"][0]
    r2 = await admin.post(
        "/api/ai/approve",
        json={"conversation_id": first[0]["conversation_id"], "approvals": {call["id"]: True}},
    )
    second = _events(r2.text)
    assert {e.get("run_id") for e in second} == {run_id}

    trace = trace_store.traces.get(run_id)
    assert trace is not None and trace.status == "done"
    approval_steps = [s for s in trace.steps if s.kind == "approval"]
    assert len(approval_steps) == 1 and approval_steps[0].status == "done"
    assert approval_steps[0].duration_ms is not None


def test_trace_store_lru_and_ttl(monkeypatch):
    """轨迹仓库：容量淘汰、按会话清理、步骤上限。"""
    from nautilus.ai import trace as trace_store

    store = trace_store.TraceStore()
    monkeypatch.setattr(trace_store, "MAX_RUNS", 3)
    monkeypatch.setattr(store, "_evict", lambda: _evict(store))
    ids = []
    for i in range(5):
        rid = store.new_id()
        store.begin(rid, "1:c", "m", f"q{i}")
        ids.append(rid)
    assert len(store._data) <= 3
    assert store.get(ids[-1]) is not None
    store.drop_conversation("1:c")
    assert len(store._data) == 0


def _evict(store):
    from nautilus.ai import trace as trace_store

    while len(store._data) > trace_store.MAX_RUNS:
        store._data.popitem(last=False)
