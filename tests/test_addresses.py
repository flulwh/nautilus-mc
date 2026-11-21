"""地址配置：面板对外地址、MCP 监听地址、每台服务器的进服地址。"""

from __future__ import annotations

import pytest

from nautilus.addresses import AddressError, Addresses, _client_example, public


def test_addresses_defaults_are_safe():
    cfg = Addresses().clamp()
    assert cfg.mcp_mode == "stdio"
    assert cfg.mcp_host == "127.0.0.1"
    assert cfg.panel_url == ""


@pytest.mark.parametrize(
    "bad",
    [
        {"panel_url": "ftp://example.com"},
        {"mcp_mode": "websocket"},
        {"mcp_port": 80},
        {"panel_url": "x" * 400},
    ],
)
def test_addresses_reject_bad_values(bad):
    with pytest.raises(AddressError):
        Addresses(**bad).clamp()


def test_addresses_normalise():
    cfg = Addresses(panel_url="  https://mc.example.com/  ", mcp_host=" ", mcp_port="9000").clamp()  # type: ignore[arg-type]
    assert cfg.panel_url == "https://mc.example.com"
    assert cfg.mcp_host == "127.0.0.1"
    assert cfg.mcp_port == 9000


def test_public_infers_from_request():
    class URL:
        scheme = "http"
        netloc = "127.0.0.1:8199"

    class Request:
        headers = {"host": "10.0.0.5:8199"}
        url = URL()

    view = public(Addresses(), Request())
    assert view["inferred"] is True
    assert view["effective_panel_url"] == "http://10.0.0.5:8199"

    view = public(Addresses(panel_url="https://mc.example.com"), Request())
    assert view["inferred"] is False
    assert view["effective_panel_url"] == "https://mc.example.com"


def test_client_example_matches_mode():
    assert "-m" in _client_example(Addresses(mcp_mode="stdio"))
    assert "streamable-http" in _client_example(Addresses(mcp_mode="http"))


def test_exposure_warning():
    assert public(Addresses(mcp_host="127.0.0.1"))["mcp_exposed"] is False
    assert public(Addresses(mcp_host="0.0.0.0"))["mcp_exposed"] is True


async def test_addresses_api_roundtrip(admin):
    r = await admin.get("/api/system/addresses")
    assert r.status_code == 200
    assert r.json()["mcp_mode"] == "stdio"

    r = await admin.put(
        "/api/system/addresses",
        json={
            "panel_url": "https://mc.example.com",
            "mcp_mode": "http",
            "mcp_host": "127.0.0.1",
            "mcp_port": 9001,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["effective_panel_url"] == "https://mc.example.com"
    assert body["mcp_client_example"].find("9001") > 0

    # 非法地址要被拦下，且不能污染已存配置
    r = await admin.put("/api/system/addresses", json={"panel_url": "ftp://bad"})
    assert r.status_code == 400
    r = await admin.get("/api/system/addresses")
    assert r.json()["panel_url"] == "https://mc.example.com"


async def test_server_public_address_roundtrip(admin):
    r = await admin.post(
        "/api/servers",
        json={
            "name": "addr-test",
            "kind": "paper",
            "version": "1.20.4",
            "memory_mb": 1024,
            "port": 0,
            "install": False,
        },
    )
    assert r.status_code == 201, r.text
    sid = r.json()["id"]

    r = await admin.patch(f"/api/servers/{sid}", json={"public_address": "mc.example.com:25565"})
    assert r.status_code == 200, r.text
    assert r.json()["public_address"] == "mc.example.com:25565"

    r = await admin.patch(f"/api/servers/{sid}", json={"public_address": "https://bad/path"})
    assert r.status_code == 400

    r = await admin.get(f"/api/servers/{sid}")
    assert r.json()["public_address"] == "mc.example.com:25565"


async def test_ai_tools_expose_addresses(admin):
    """AI 工具必须能读到地址，否则回答“怎么进服”只能瞎猜。"""
    from nautilus.ai import tools

    token = tools.set_ctx(tools.Ctx(role="admin", username="tester"))
    try:
        info = await tools.TOOLS["panel_info"].fn()
        assert info["ok"] is True
        assert "panel_url" in info

        servers = await tools.TOOLS["list_servers"].fn()
        assert servers["ok"] is True
        assert all("public_address" in item for item in servers["servers"])
    finally:
        tools.reset_ctx(token)
