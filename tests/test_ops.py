"""Curated operations actions: save/reload/broadcast/maintenance."""

from __future__ import annotations

import json


async def _make_server(client, name="ops"):
    payload = {"name": name, "kind": "paper", "version": "1.21.4", "memory_mb": 1024, "install": False}
    r = await client.post("/api/servers", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


async def test_actions_require_running_server(admin):
    server = await _make_server(admin, name="ops-down")
    sid = server["id"]
    r = await admin.post(f"/api/servers/{sid}/actions/save")
    assert r.status_code == 409
    r = await admin.post(f"/api/servers/{sid}/actions/broadcast", json={"message": "hi"})
    assert r.status_code == 409
    r = await admin.post(f"/api/servers/{sid}/actions/maintenance", json={"enabled": False})
    assert r.status_code == 409
    await admin.delete(f"/api/servers/{sid}", params={"purge": True})


async def test_broadcast_flattens_newlines(admin, monkeypatch):
    from nautilus.servers import ops

    commands: list[str] = []

    async def fake_send(server, command):
        commands.append(command)
        return "done"

    monkeypatch.setattr(ops.manager, "send", fake_send)
    server = await _make_server(admin, name="ops-bcast")
    r = await admin.post(
        f"/api/servers/{server['id']}/actions/broadcast",
        json={"message": "晚上活动\n/start 集合"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert "\n" not in body["message"]
    assert commands == ["say 晚上活动 /start 集合"]
    await admin.delete(f"/api/servers/{server['id']}", params={"purge": True})


async def test_maintenance_on_kicks_only_outsiders(admin, monkeypatch, tmp_path):
    from nautilus.servers import ops

    commands: list[str] = []

    async def fake_send(server, command):
        commands.append(command)
        return "done"

    class FakeConsole:
        players = {"alice", "mod", "zed"}

    class FakeRuntime:
        alive = True
        console = FakeConsole()

    class FakeManager:
        def get(self, _sid):
            return FakeRuntime()

        send = staticmethod(fake_send)

    monkeypatch.setattr(ops, "manager", FakeManager())
    server = await _make_server(admin, name="ops-maint")
    from nautilus.settings import settings

    root = settings.data_dir / "servers" / str(server["id"])
    (root / "whitelist.json").write_text(json.dumps([{"uuid": "u1", "name": "alice"}]), encoding="utf-8")
    (root / "ops.json").write_text(json.dumps([{"uuid": "u2", "name": "mod"}]), encoding="utf-8")

    r = await admin.post(
        f"/api/servers/{server['id']}/actions/maintenance",
        json={"enabled": True, "kick_outsiders": True, "reason": "维护"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["enabled"] is True
    assert body["kicked"] == ["zed"]
    assert "whitelist on" in commands and "save-all" in commands
    assert any(c.startswith("kick zed") for c in commands)
    # The JVM owns server.properties while running; the panel must not write it.
    props_file = root / "server.properties"
    assert "white-list=true" not in (props_file.read_text(encoding="utf-8") if props_file.exists() else "")
    await admin.delete(f"/api/servers/{server['id']}", params={"purge": True})


async def test_ops_ai_tools_registered_with_permissions():
    from nautilus.ai.tools import TOOLS

    expected = {
        "broadcast_announcement": (("server", "console"), True, False),
        "save_world": (("server", "console"), True, False),
        "reload_server": (("server", "console"), True, True),
        "set_maintenance_mode": (("server", "console"), True, True),
    }
    for name, (perm, mutates, dangerous) in expected.items():
        spec = TOOLS[name]
        assert tuple(spec.perm) == perm, name
        assert bool(spec.mutates) == mutates, name
        assert bool(spec.dangerous) == dangerous, name


async def test_ops_tools_delegate_to_manager(admin, monkeypatch):
    from nautilus.ai import tools as ai_tools
    from nautilus.servers import ops

    commands: list[str] = []

    async def fake_send(server, command):
        commands.append(command)
        return "ok"

    class FakeManager:
        def get(self, _sid):
            class RT:
                alive = True

                class console:
                    players: set[str] = set()

            return RT()

        send = staticmethod(fake_send)

    monkeypatch.setattr(ops, "manager", FakeManager())
    server = await _make_server(admin, name="ops-tools")
    token = ai_tools.set_ctx(ai_tools.Ctx(role="admin", username="tester"))
    try:
        r = await ai_tools.TOOLS["broadcast_announcement"].fn(server["id"], "集结\n开始")
        assert r["ok"] is True and r["message"] == "集结 开始"
        r = await ai_tools.TOOLS["save_world"].fn(server["id"])
        assert r["ok"] is True
        r = await ai_tools.TOOLS["set_maintenance_mode"].fn(server["id"], False)
        assert r["ok"] is True and r["enabled"] is False
    finally:
        ai_tools.reset_ctx(token)
        await admin.delete(f"/api/servers/{server['id']}", params={"purge": True})
    assert commands == ["say 集结 开始", "save-all", "whitelist off"]
