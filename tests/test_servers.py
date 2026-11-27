"""Server CRUD, file browsing, config editing and console parsing."""

from __future__ import annotations

import json



async def _make_server(client, name="survival", **extra):
    payload = {"name": name, "kind": "paper", "version": "1.21.4", "memory_mb": 1024, "install": False}
    payload.update(extra)
    r = await client.post("/api/servers", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


async def test_server_crud(admin):
    server = await _make_server(admin)
    sid = server["id"]
    assert server["runtime"]["state"] == "stopped"
    # RCON must have its own port: echoing the game port makes the server fail
    # to bind on startup.
    assert server["rcon_port"] != server["port"]
    assert 1024 <= server["rcon_port"] <= 65535

    r = await admin.get("/api/servers")
    assert r.status_code == 200
    assert any(s["id"] == sid for s in r.json())

    r = await admin.patch(f"/api/servers/{sid}", json={"memory_mb": 4096})
    assert r.status_code == 200
    assert r.json()["memory_mb"] == 4096

    r = await admin.post("/api/servers", json={"name": "survival", "kind": "paper"})
    assert r.status_code == 409

    r = await admin.delete(f"/api/servers/{sid}", params={"purge": True})
    assert r.status_code == 204
    r = await admin.get(f"/api/servers/{sid}")
    assert r.status_code == 404


async def test_start_without_jar_is_rejected(admin):
    server = await _make_server(admin, name="nojar")
    r = await admin.post(f"/api/servers/{server['id']}/start")
    assert r.status_code == 409
    assert "jar" in r.json()["detail"].lower()
    await admin.delete(f"/api/servers/{server['id']}", params={"purge": True})


async def test_console_requires_running(admin):
    server = await _make_server(admin, name="cmd")
    r = await admin.post(f"/api/servers/{server['id']}/command", json={"command": "list"})
    assert r.status_code == 409
    await admin.delete(f"/api/servers/{server['id']}", params={"purge": True})


async def test_file_browser_roundtrip(admin):
    server = await _make_server(admin, name="files")
    sid = server["id"]

    r = await admin.get(f"/api/servers/{sid}/files")
    assert r.status_code == 200
    assert {e["name"] for e in r.json()["entries"]} >= {"plugins", "logs"}

    r = await admin.put(
        f"/api/servers/{sid}/files/content",
        json={"path": "plugins/test.yml", "content": "hello: world\n"},
    )
    assert r.status_code == 200, r.text

    r = await admin.get(f"/api/servers/{sid}/files/content", params={"path": "plugins/test.yml"})
    assert r.json()["content"] == "hello: world\n"

    r = await admin.post(f"/api/servers/{sid}/files/mkdir", json={"path": "plugins/sub"})
    assert r.status_code == 201

    r = await admin.post(f"/api/servers/{sid}/files/rename", json={"path": "plugins/test.yml", "name": "renamed.yml"})
    assert r.status_code == 200
    assert r.json()["path"] == "plugins/renamed.yml"

    r = await admin.delete(f"/api/servers/{sid}/files", params={"path": "plugins/sub"})
    assert r.status_code == 200

    await admin.delete(f"/api/servers/{sid}", params={"purge": True})


async def test_path_traversal_is_blocked(admin):
    server = await _make_server(admin, name="escape")
    sid = server["id"]
    r = await admin.get(f"/api/servers/{sid}/files/content", params={"path": "../../../../etc/passwd"})
    assert r.status_code in {400, 404}
    r = await admin.get(f"/api/servers/{sid}/files", params={"path": "../"})
    assert r.status_code == 400
    await admin.delete(f"/api/servers/{sid}", params={"purge": True})


async def test_config_edit_keeps_comments(admin):
    server = await _make_server(admin, name="config")
    sid = server["id"]
    path = f"/api/servers/{sid}/files/content"
    yaml_text = "# top comment\nmotd: hello  # inline\nnested:\n  a: 1\n  b: 2\n"
    await admin.put(path, json={"path": "paper-global.yml", "content": yaml_text})

    r = await admin.get(f"/api/servers/{sid}/config/files")
    assert r.status_code == 200
    assert "paper-global.yml" in r.json()["files"]

    r = await admin.get(f"/api/servers/{sid}/config/paper-global.yml")
    assert r.status_code == 200
    data = r.json()
    assert data["format"] == "yaml"
    assert data["data"]["motd"] == "hello"

    r = await admin.put(f"/api/servers/{sid}/config/paper-global.yml", json={"data": {"nested": {"b": 99}}})
    assert r.status_code == 200, r.text

    raw = (await admin.get(path, params={"path": "paper-global.yml"})).json()["content"]
    assert "# top comment" in raw, raw
    assert "# inline" in raw, raw
    assert "b: 99" in raw

    backups = (await admin.get(f"/api/servers/{sid}/config/paper-global.yml/backups")).json()["backups"]
    assert backups

    await admin.delete(f"/api/servers/{sid}", params={"purge": True})


async def test_properties_edit(admin):
    server = await _make_server(admin, name="props")
    sid = server["id"]
    await admin.put(
        f"/api/servers/{sid}/files/content",
        json={"path": "server.properties", "content": "server-port=25599\nmotd=Test\nonline-mode=true\n"},
    )
    r = await admin.get(f"/api/servers/{sid}/config/server.properties")
    assert r.json()["data"]["server-port"] == "25599"
    assert r.json()["types"]["server-port"] == "int"

    r = await admin.put(
        f"/api/servers/{sid}/config/server.properties",
        json={"data": {"motd": "A Nautilus Server", "max-players": "50"}},
    )
    assert r.status_code == 200
    assert r.json()["data"]["motd"] == "A Nautilus Server"
    assert r.json()["data"]["max-players"] == "50"
    assert r.json()["data"]["server-port"] == "25599"

    await admin.delete(f"/api/servers/{sid}", params={"purge": True})


def test_console_parser():
    from nautilus.servers.logbus import ConsoleBuffer, parse

    buf = ConsoleBuffer(capacity=10)
    buf.push("[12:00:00] [Server thread/INFO]: Done (3.215s)! For help, type \"help\"")
    assert parse("[12:00:00] [Server thread/INFO]: Steve joined the game").event == "join"
    buf.push("[12:00:01] [Server thread/INFO]: Steve joined the game")
    assert buf.players == {"Steve"}
    buf.push("[12:00:02] [Server thread/INFO]: Steve left the game")
    assert buf.players == set()
    p = buf.push("[12:00:03] [Server thread/ERROR]: Something exploded")
    assert p.level == "error"
    assert "exploded" in buf.last_error


def test_tps_parser():
    from nautilus.servers.logbus import parse_tps

    paper = "TPS from last 1m, 5m, 10m: 19.8, 18.2, 17.0\nMemory: 814M / 2048M (39.75%)"
    out = parse_tps(paper)
    assert out["tps1"] == 19.8 and out["tps5"] == 18.2 and out["tps10"] == 17.0
    assert out["mem_pct"] == 39.75
    assert out["mspt"] is None

    # Real 1.20.6+ Paper reply: colour codes and a 15-minute window.
    modern = "§6TPS from last 1m, 5m, 15m: §a19.2§r, §a18.0§r, §a17.5\n"
    out = parse_tps(modern)
    assert out["tps1"] == 19.2 and out["tps5"] == 18.0 and out["tps10"] == 17.5

    spigot = "This server is running CraftBukkit...\nMean tick time: 12.345ms\nTPS from last 1m, 5m, 10m: 20.0, 20.0, 20.0"
    out = parse_tps(spigot)
    assert out["mspt"] == 12.345
    assert out["tps1"] == 20.0

    assert parse_tps("Unknown or incomplete command") is None


def test_jfr_check_parser_and_paths(tmp_path):
    import os as _os

    from nautilus.models import Server
    from nautilus.servers import jfr

    text = (
        "14132:\n"
        "Recording 1: name=nautilus-20261127-222101 duration=30s (running)\n"
        "Recording 2: name=nautilus-old (stopped)\n"
        "legacy-1: (running) 5.2 s\n"
    )
    recs = jfr.parse_check(text)
    assert [r["name"] for r in recs] == ["nautilus-20261127-222101", "nautilus-old", "legacy-1"]
    assert recs[0]["running"] and recs[0]["detail"] == "duration=30s"
    assert not recs[1]["running"]
    assert recs[2]["running"]

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = "jcmd.exe" if _os.name == "nt" else "jcmd"
    (bin_dir / exe).write_text("stub")
    server = Server(name="x", kind="paper", java=str(bin_dir / ("java.exe" if _os.name == "nt" else "java")))
    assert jfr.jcmd_path(server) == str(bin_dir / exe)


def test_jfr_filename_guard(tmp_path):
    from nautilus.models import Server
    from nautilus.servers import jfr

    server = Server(name="x", kind="paper")
    for bad in ("../../etc/passwd", "logs/evil.jfr", "other.jfr", "nautilus-jfr-a.bin"):
        try:
            jfr._target_file(server, bad)
        except jfr.JfrError:
            continue
        raise AssertionError(f"accepted {bad!r}")


def test_command_builder():
    from nautilus.models import Server
    from nautilus.servers.manager import manager

    s = Server(name="x", kind="paper", jar="paper.jar", memory_mb=2048, min_memory_mb=512, jvm_args="-XX:+UseG1GC", server_args="--nogui")
    argv = manager.build_command(s)
    assert argv[1] == "-Xms512M"
    assert argv[2] == "-Xmx2048M"
    assert "-XX:+UseG1GC" in argv
    assert argv[-1] == "--nogui"
    assert argv[argv.index("-jar") + 1] == "paper.jar"


def test_runtime_snapshot_shape():
    from nautilus.servers.manager import manager

    snap = manager.snapshot(4242)
    assert snap["state"] == "stopped"
    assert snap["players"] == []
    json.dumps(snap)  # must be JSON serialisable for the API
