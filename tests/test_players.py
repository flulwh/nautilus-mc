"""Player administration: offline UUIDs, whitelist/ops/ban files, RCON fallback."""

from __future__ import annotations

import json

from nautilus.resources.players import offline_uuid
from nautilus.settings import settings


async def _offline_server(client, name="players-host"):
    """Create a server whose properties say online-mode=false (no Mojang call)."""
    r = await client.post("/api/servers", json={"name": name, "kind": "paper", "version": "1.21.4", "install": False})
    assert r.status_code == 201, r.text
    data = r.json()
    props = settings.servers_dir / str(data["id"]) / "server.properties"
    props.write_text(
        "level-name=world\nonline-mode=false\nwhite-list=false\n",
        encoding="utf-8",
    )
    return data


def _read(sid: int, filename: str):
    path = settings.servers_dir / str(sid) / filename
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


async def test_offline_uuid_matches_minecraft_derivation():
    # Reference value computed by Minecraft for the offline player "Notch".
    assert offline_uuid("Notch") == "b50ad385-829d-3141-a216-7e7d7539ba7f"


async def test_whitelist_add_and_remove(admin):
    server = await _offline_server(admin)
    sid = server["id"]

    r = await admin.get(f"/api/servers/{sid}/players")
    assert r.status_code == 200, r.text
    assert r.json()["online"] == []
    assert r.json()["whitelist_enabled"] is False
    assert r.json()["online_mode"] is False

    r = await admin.post(f"/api/servers/{sid}/players/whitelist", json={"name": "Steve"})
    assert r.status_code == 201, r.text
    payload = r.json()
    assert payload["source"] == "offline"
    assert payload["entry"]["uuid"] == offline_uuid("Steve")
    assert [p["name"] for p in payload["whitelist"]] == ["Steve"]
    assert payload["applied"] is False
    assert _read(sid, "whitelist.json")[0]["name"] == "Steve"

    # Idempotent: a second add must not duplicate the row.
    r = await admin.post(f"/api/servers/{sid}/players/whitelist", json={"name": "Steve"})
    assert len(r.json()["whitelist"]) == 1

    r = await admin.delete(f"/api/servers/{sid}/players/whitelist/Steve")
    assert r.status_code == 200
    assert r.json()["removed"] is True
    assert _read(sid, "whitelist.json") == []

    await admin.delete(f"/api/servers/{sid}", params={"purge": True})


async def test_invalid_player_name_is_rejected(admin):
    server = await _offline_server(admin, "players-bad")
    r = await admin.post(f"/api/servers/{server['id']}/players/whitelist", json={"name": "bad name!"})
    assert r.status_code == 400
    assert "玩家名" in r.json()["detail"]
    await admin.delete(f"/api/servers/{server['id']}", params={"purge": True})


async def test_ops_and_bans_roundtrip(admin):
    server = await _offline_server(admin, "players-ops")
    sid = server["id"]

    r = await admin.post(f"/api/servers/{sid}/players/ops", json={"name": "Alex", "level": 3})
    assert r.status_code == 201, r.text
    entry = r.json()["entry"]
    assert entry["level"] == 3
    assert entry["bypassesPlayerLimit"] is False
    assert _read(sid, "ops.json")[0]["uuid"] == offline_uuid("Alex")

    r = await admin.delete(f"/api/servers/{sid}/players/ops/Alex")
    assert r.json()["removed"] is True
    assert _read(sid, "ops.json") == []

    r = await admin.post(f"/api/servers/{sid}/players/bans", json={"name": "Griefer", "reason": "炸家", "kick": False})
    assert r.status_code == 201, r.text
    assert r.json()["applied"] is False  # server is stopped: file only
    ban = _read(sid, "banned-players.json")[0]
    assert ban["reason"] == "炸家"
    assert ban["expires"] == "forever"
    assert ban["uuid"] == offline_uuid("Griefer")

    r = await admin.delete(f"/api/servers/{sid}/players/bans/Griefer")
    assert r.json()["removed"] is True
    assert _read(sid, "banned-players.json") == []

    await admin.delete(f"/api/servers/{sid}", params={"purge": True})


async def test_kick_requires_a_running_server(admin):
    server = await _offline_server(admin, "players-kick")
    r = await admin.post(f"/api/servers/{server['id']}/players/kick", json={"name": "Steve"})
    assert r.status_code == 409
    await admin.delete(f"/api/servers/{server['id']}", params={"purge": True})


async def test_whitelist_mode_writes_properties(admin):
    server = await _offline_server(admin, "players-mode")
    sid = server["id"]

    r = await admin.post(f"/api/servers/{sid}/players/whitelist-mode", json={"enabled": True})
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is True
    props = (settings.servers_dir / str(sid) / "server.properties").read_text(encoding="utf-8")
    assert "white-list=true" in props
    assert "enforce-whitelist=true" in props

    r = await admin.post(f"/api/servers/{sid}/players/whitelist-mode", json={"enabled": False})
    props = (settings.servers_dir / str(sid) / "server.properties").read_text(encoding="utf-8")
    assert "white-list=false" in props

    await admin.delete(f"/api/servers/{sid}", params={"purge": True})


async def test_corrupt_whitelist_is_reported(admin):
    server = await _offline_server(admin, "players-corrupt")
    sid = server["id"]
    (settings.servers_dir / str(sid) / "whitelist.json").write_text("{not json", encoding="utf-8")
    r = await admin.get(f"/api/servers/{sid}/players")
    assert r.status_code == 422
    assert "whitelist.json" in r.json()["detail"]
    await admin.delete(f"/api/servers/{sid}", params={"purge": True})


async def test_rcon_is_applied_before_the_file_is_rewritten(admin, monkeypatch):
    """The server dumps its in-memory list to JSON when RCON runs.

    If the panel wrote the file first, that dump would erase the entry. This
    pins the ordering: reload -> rcon -> file mutation.
    """
    from nautilus.api import players as players_api
    from nautilus.resources import players as player_svc

    server = await _offline_server(admin, "players-order")
    sid = server["id"]
    order: list[tuple[str, str]] = []

    async def fake_reload(_server):
        order.append(("reload", ""))

    async def fake_rcon(_server, command):
        order.append(("rcon", command))
        return True

    original_add = player_svc.whitelist_add

    def spying_add(srv, profile):
        order.append(("file", profile["name"]))
        return original_add(srv, profile)

    monkeypatch.setattr(players_api, "_reload_whitelist", fake_reload)
    monkeypatch.setattr(players_api, "_rcon", fake_rcon)
    monkeypatch.setattr(player_svc, "whitelist_add", spying_add)

    r = await admin.post(f"/api/servers/{sid}/players/whitelist", json={"name": "Steve"})
    assert r.status_code == 201, r.text
    assert order == [("reload", ""), ("rcon", "whitelist add Steve"), ("file", "Steve")]

    await admin.delete(f"/api/servers/{sid}", params={"purge": True})
