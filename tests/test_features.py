"""Tests for the features added in the "complete the panel" pass.

Covered: world management, API tokens, notification normalisation, Java
detection helpers, Forge/NeoForge version plumbing and the new task kinds.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest


async def _create_server(client, name: str, kind: str = "vanilla") -> dict:
    r = await client.post(
        "/api/servers",
        json={"name": name, "kind": kind, "version": "", "install": False, "eula_accepted": True},
    )
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    detail = await client.get(f"/api/servers/{sid}")
    assert detail.status_code == 200
    return detail.json()


# --------------------------------------------------------------------- worlds


async def test_world_lifecycle(admin):
    server = await _create_server(admin, "worlds-a")
    root = Path(server["directory"])
    (root / "world" / "region").mkdir(parents=True, exist_ok=True)
    (root / "world" / "level.dat").write_bytes(b"level")
    (root / "world_s2" / "region").mkdir(parents=True, exist_ok=True)
    (root / "world_s2" / "level.dat").write_bytes(b"level")

    listing = await admin.get(f"/api/servers/{server['id']}/worlds")
    assert listing.status_code == 200
    body = listing.json()
    names = {w["name"] for w in body["worlds"]}
    assert {"world", "world_s2"} <= names
    assert body["settings"]["level-name"] == "world"
    assert body["running"] is False

    # Switch the active world.
    r = await admin.post(f"/api/servers/{server['id']}/worlds/world_s2/activate")
    assert r.status_code == 200, r.text
    assert r.json()["values"]["level-name"] == "world_s2"

    # The active world refuses deletion; the other one is moved to trash.
    r = await admin.delete(f"/api/servers/{server['id']}/worlds/world_s2")
    assert r.status_code == 400
    r = await admin.delete(f"/api/servers/{server['id']}/worlds/world")
    assert r.status_code == 204, r.text
    assert not (root / "world").exists()
    trash = list((root / ".nautilus" / "world-trash").iterdir())
    assert trash and (trash[0] / "level.dat").exists()

    # Creating a new world switches level-name and lists it as pending.
    r = await admin.post(f"/api/servers/{server['id']}/worlds", json={"name": "fresh", "seed": "42"})
    assert r.status_code == 201, r.text
    listing = (await admin.get(f"/api/servers/{server['id']}/worlds")).json()
    pending = [w for w in listing["worlds"] if w["name"] == "fresh"]
    assert pending and pending[0]["pending"] is True and pending[0]["active"] is True


async def test_world_settings_validation(admin):
    server = await _create_server(admin, "worlds-b")
    bad = await admin.put(f"/api/servers/{server['id']}/worlds/settings", json={"view-distance": 999})
    assert bad.status_code == 400
    bad = await admin.put(f"/api/servers/{server['id']}/worlds/settings", json={"level-name": "../etc"})
    assert bad.status_code == 400
    good = await admin.put(
        f"/api/servers/{server['id']}/worlds/settings",
        json={"difficulty": "hard", "pvp": False, "max-players": 42, "motd": "hello\nworld"},
    )
    assert good.status_code == 200, good.text
    values = good.json()["values"]
    assert values["difficulty"] == "hard"
    assert values["pvp"] is False
    assert values["max-players"] == 42
    assert values["motd"] == "hello world"


# --------------------------------------------------------------------- tokens


async def test_api_token_roundtrip(app, admin):
    created = await admin.post("/api/tokens", json={"name": "ci", "days": 30})
    assert created.status_code == 201, created.text
    raw = created.json()["token"]
    assert raw.startswith("nlt_")
    token_id = created.json()["meta"]["id"]

    # A fresh client with only the bearer header is authenticated as the owner.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as anon:
        me = await anon.get("/api/auth/me", headers={"Authorization": f"Bearer {raw}"})
        assert me.status_code == 200, me.text
        assert me.json()["user"]["username"] == "admin"
        servers = await anon.get("/api/servers", headers={"Authorization": f"Bearer {raw}"})
        assert servers.status_code == 200

        # A malformed token is rejected instead of silently falling back.
        bad = await anon.get("/api/auth/me", headers={"Authorization": "Bearer nlt_not-a-real-token"})
        assert bad.status_code == 401

    assert (await admin.delete(f"/api/tokens/{token_id}")).status_code == 204
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as anon:
        revoked = await anon.get("/api/auth/me", headers={"Authorization": f"Bearer {raw}"})
        assert revoked.status_code == 401


async def test_token_cannot_be_revoked_by_another_user(app, admin):
    created = await admin.post("/api/tokens", json={"name": "mine", "days": 0})
    token_id = created.json()["meta"]["id"]

    # Create a plain user, log in as them in a separate client.
    r = await admin.post(
        "/api/users",
        json={"username": "tokenviewer", "email": "tokenviewer@example.com", "password": "ViewerPass123", "role": "viewer"},
    )
    assert r.status_code == 201, r.text
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as other:
        login = await other.post("/api/auth/signin", json={"identifier": "tokenviewer", "password": "ViewerPass123"})
        assert login.status_code == 204, login.text
        denied = await other.delete(f"/api/tokens/{token_id}")
        assert denied.status_code == 404


# ---------------------------------------------------------------- notifications


def test_webhook_normalisation_drops_junk():
    from nautilus import notify

    items = notify.normalise(
        [
            {"url": "https://example.com/hook", "name": "ok", "events": ["server.crashed", "bogus"]},
            {"url": "ftp://nope", "name": "bad"},
            "not-a-dict",
            {"url": "https://discord.com/api/webhooks/1/x", "events": None},
        ]
    )
    assert len(items) == 2
    assert items[0]["events"] == ["server.crashed"]
    assert items[1]["events"] == ["*"]
    assert items[0]["id"].startswith("wh_")


def test_discord_detection():
    from nautilus import notify

    assert notify._is_discord("https://discord.com/api/webhooks/1/x", "auto")
    assert not notify._is_discord("https://example.com", "auto")
    assert notify._is_discord("https://example.com", "discord")
    assert not notify._is_discord("https://discord.com/api/webhooks/1/x", "json")


def test_notify_emit_without_loop_is_silent():
    from nautilus import notify

    # Sync context: must not raise, must not create a task.
    notify.emit("server.crashed", server_id=1, detail={"code": 1})


# ------------------------------------------------------------------------ java


def test_java_major_parsing():
    from nautilus.servers import java

    assert java._major_of("1.8.0_402") == 8
    assert java._major_of("17.0.11") == 17
    assert java._major_of("21.0.13.1+2") == 21
    assert java._major_of("25-ea") == 25
    assert java._major_of("garbage") == 0


def test_java_refuses_to_delete_system_runtime(tmp_path, monkeypatch):
    from nautilus.servers import java

    monkeypatch.setattr(java, "managed_root", lambda: tmp_path / "jre")
    (tmp_path / "jre").mkdir()
    outside = tmp_path / "system-java" / "bin" / "java"
    outside.parent.mkdir(parents=True)
    outside.write_text("")
    with pytest.raises(java.JavaError):
        java.remove(str(outside))


# -------------------------------------------------------------------- provision


def test_forge_artifact_naming(monkeypatch):
    from nautilus.servers import provision

    async def promos():
        return {"1.20.4-recommended": "49.2.13"}

    class FakeResponse:
        status_code = 200
        headers = {"content-length": "900000"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def head(self, url):
            assert "1.20.4-49.2.13/forge-1.20.4-49.2.13-installer.jar" in url
            return FakeResponse()

    async def fake_client():
        return FakeClient()

    monkeypatch.setattr(provision, "forge_promotions", promos)
    monkeypatch.setattr(provision, "_client", fake_client)
    art = __import__("asyncio").run(provision.forge_artifact("1.20.4"))
    assert art.install is True
    assert art.loader_version == "49.2.13"
    assert "forge-1.20.4-49.2.13-shim.jar" in art.entry


def test_neoforge_mc_mapping():
    from nautilus.servers import provision

    assert provision.neoforge_mc("21.1.72") == "1.21.1"
    assert provision.neoforge_mc("20.4.237") == "1.20.4"
    assert provision.neoforge_mc("bad") == ""


def test_version_sort_is_numeric():
    from nautilus.servers import provision

    versions = ["1.9.4", "1.10.2", "1.20.4", "1.21"]
    assert sorted(versions, key=provision._verkey) == ["1.9.4", "1.10.2", "1.20.4", "1.21"]


# ---------------------------------------------------------------- task kinds


async def test_kill_task_kind_accepted(admin):
    server = await _create_server(admin, "killer")
    r = await admin.post(
        f"/api/servers/{server['id']}/tasks",
        json={"name": "force stop", "kind": "kill", "cron": "0 5 * * *"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["kind"] == "kill"


def test_managed_runtime_is_discovered(tmp_path, monkeypatch):
    """Temurin archives keep a wrapper directory; discovery must look inside it."""
    from nautilus.servers import java

    exe = tmp_path / "jre" / "21-21.0.13" / "jdk-21.0.13.1+2-jre" / "bin" / ("java.exe" if __import__("os").name == "nt" else "java")
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    monkeypatch.setattr(java.settings, "data_dir", tmp_path)
    monkeypatch.setattr(java.shutil, "which", lambda _: None)

    candidates = java._candidates()
    assert str(exe) in candidates


def test_proxy_flags_follow_environment(monkeypatch):
    from nautilus.servers import provision

    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    args = provision._java_proxy_args()
    assert "-Dhttps.proxyHost=127.0.0.1" in args
    assert "-Dhttps.proxyPort=7890" in args

    monkeypatch.delenv("HTTPS_PROXY")
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    assert provision._java_proxy_args() == []
