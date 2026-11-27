"""Regression tests for the defects found in the industrial bug sweep.

Each test names the failure mode it locks down, so a future refactor that
reintroduces it fails loudly instead of shipping.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import socket
import tarfile
import time
import types

import httpx
import pytest

ADMIN_PASSWORD = "NautilusAdmin2026"


async def _make_server(client, name, **extra):
    payload = {"name": name, "kind": "paper", "version": "1.21.4", "memory_mb": 1024, "install": False}
    payload.update(extra)
    r = await client.post("/api/servers", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


# --------------------------------------------------------------------- RBAC


async def test_me_returns_expanded_permissions(app, admin):
    """The UI compares concrete ``server:control`` strings.

    Returning the raw policy regex (``server:read|control|console``) meant an
    operator saw no control buttons at all.
    """
    r = await admin.post(
        "/api/users",
        json={"username": "op1", "email": "op1@example.com", "password": "OperatorPass1", "role": "operator"},
    )
    assert r.status_code == 201, r.text

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as op:
        login = await op.post("/api/auth/login", data={"username": "op1@example.com", "password": "OperatorPass1"})
        assert login.status_code == 204, login.text
        me = (await op.get("/api/auth/me")).json()
        perms = set(me["permissions"])
        assert {"server:control", "server:read", "server:console"} <= perms
        assert "server:delete" not in perms  # operator cannot delete servers
        assert "users:manage" not in perms
        # The same permissions must be enforced by the API, not just displayed.
        assert (await op.get("/api/servers")).status_code == 200
        assert (await op.delete("/api/servers/1")).status_code == 403


# ------------------------------------------------------------------- users


async def test_delete_user_with_audit_history(app, admin):
    """Audit rows reference users.id; deleting an account used to 500."""
    r = await admin.post(
        "/api/users",
        json={"username": "audited", "email": "audited@example.com", "password": "AuditedPass1", "role": "viewer"},
    )
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    # Log in once (on its own client, so the admin cookie is preserved) via the
    # panel's own signin endpoint, which is what writes an ``auth.login`` row.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as user:
        assert (
            await user.post("/api/auth/signin", json={"identifier": "audited", "password": "AuditedPass1"})
        ).status_code == 204

    assert (await admin.delete(f"/api/users/{uid}")).status_code == 204
    # The history survives, detached from the deleted account.
    entries = (await admin.get("/api/audit", params={"actor": "audited"})).json()
    assert entries["entries"], entries
    assert all(e["actor"] == "audited" for e in entries["entries"])


# ------------------------------------------------------------------ servers


async def test_server_ports_must_be_distinct(admin):
    server = await _make_server(admin, "ports")
    sid = server["id"]
    r = await admin.patch(f"/api/servers/{sid}", json={"rcon_port": server["port"]})
    assert r.status_code == 400
    r = await admin.patch(f"/api/servers/{sid}", json={"port": 70000})
    assert r.status_code == 422
    await admin.delete(f"/api/servers/{sid}", params={"purge": True})


async def test_second_server_gets_free_rcon_port(admin):
    first = await _make_server(admin, "first")
    second = await _make_server(admin, "second")
    assert second["port"] != first["port"]
    assert second["rcon_port"] not in {first["port"], first["rcon_port"], second["port"]}
    for item in (first, second):
        await admin.delete(f"/api/servers/{item['id']}", params={"purge": True})


async def test_jar_path_cannot_escape_server_dir(admin):
    server = await _make_server(admin, "jarpath")
    r = await admin.patch(f"/api/servers/{server['id']}", json={"jar": "../../evil.jar"})
    assert r.status_code == 422
    await admin.delete(f"/api/servers/{server['id']}", params={"purge": True})


async def test_unknown_server_logs_are_404(admin):
    assert (await admin.get("/api/servers/999999/logs")).status_code == 404
    assert (await admin.get("/api/servers/999999/metrics")).status_code == 404
    assert (await admin.get("/api/servers/999999/tps")).status_code == 404


async def test_tps_samples_only_paper_with_rcon(admin, monkeypatch):
    """Gate check: only Paper-family + running servers get polled for /tps."""
    from nautilus.servers import manager as mgr_mod

    calls: list[str] = []

    async def fake_send(self, server, command):
        calls.append(f"{server.name}:{command}")
        return "TPS from last 1m, 5m, 10m: 20.0, 20.0, 20.0"

    monkeypatch.setattr(mgr_mod.ServerManager, "send", fake_send)

    paper = await _make_server(admin, "tps-paper")
    fabric = await _make_server(admin, "tps-fabric", kind="fabric")

    runtimes = {}
    for sid in (paper["id"], fabric["id"]):
        rt = mgr_mod.Runtime(server_id=sid, state="running")
        rt.proc = types.SimpleNamespace(returncode=None)
        runtimes[sid] = rt
    mgr_mod.manager._rt.update(runtimes)
    try:
        await mgr_mod.manager.sample_tps()
        assert calls == ["tps-paper:tps"]
        assert runtimes[paper["id"]].tps[-1]["tps1"] == 20.0
        assert not runtimes[fabric["id"]].tps
    finally:
        for sid in runtimes:
            mgr_mod.manager._rt.pop(sid, None)
    assert (await admin.get("/api/servers/999999/tps")).status_code == 404


# -------------------------------------------------------------------- files


async def test_rename_rejects_bad_names(admin):
    server = await _make_server(admin, "rename")
    sid = server["id"]
    await admin.put(f"/api/servers/{sid}/files/content", json={"path": "a.txt", "content": "x"})
    for bad in ["", ".", "..", "sub/dir.txt", "CON"]:
        r = await admin.post(f"/api/servers/{sid}/files/rename", json={"path": "a.txt", "name": bad})
        assert r.status_code in {400, 422}, (bad, r.text)
    # Case-only rename is legal and must not be treated as a collision.
    r = await admin.post(f"/api/servers/{sid}/files/rename", json={"path": "a.txt", "name": "A.txt"})
    assert r.status_code == 200, r.text
    await admin.delete(f"/api/servers/{sid}", params={"purge": True})


# ------------------------------------------------------------------ backups


async def test_restore_rejects_archive_touching_panel_state(admin):
    server = await _make_server(admin, "restore")
    sid = server["id"]
    detail = (await admin.get(f"/api/servers/{sid}")).json()
    folder = os.path.join(detail["directory"], ".nautilus", "archives")
    os.makedirs(folder, exist_ok=True)
    name = f"{time.strftime('%Y%m%d-%H%M%S')}-full-evil.tar.gz"
    with tarfile.open(os.path.join(folder, name), "w:gz") as tf:
        payload = b"pwned"
        info = tarfile.TarInfo(".nautilus/pre-restore/evil.txt")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))

    r = await admin.post(f"/api/servers/{sid}/backups/{name}/restore")
    assert r.status_code == 409, r.text
    assert not os.path.exists(os.path.join(detail["directory"], ".nautilus", "pre-restore", "evil.txt"))
    await admin.delete(f"/api/servers/{sid}", params={"purge": True})


# ------------------------------------------------------------------- tasks


async def test_task_update_validates_cron_and_payload(admin):
    server = await _make_server(admin, "tasks")
    sid = server["id"]
    r = await admin.post(
        f"/api/servers/{sid}/tasks",
        json={"name": "say hi", "kind": "command", "cron": "0 4 * * *", "payload": "say hi"},
    )
    assert r.status_code == 201, r.text
    tid = r.json()["id"]

    assert (await admin.patch(f"/api/servers/{sid}/tasks/{tid}", json={"cron": ""})).status_code == 400
    assert (await admin.patch(f"/api/servers/{sid}/tasks/{tid}", json={"payload": "  "})).status_code == 400
    assert (await admin.patch(f"/api/servers/{sid}/tasks/{tid}", json={"cron": "0 4 * *"})).status_code == 400
    await admin.delete(f"/api/servers/{sid}", params={"purge": True})


# ------------------------------------------------------------------- audit


async def test_audit_total_respects_filters(admin):
    body = (await admin.get("/api/audit", params={"action": "server.create", "limit": 500})).json()
    assert body["entries"]
    assert body["total"] == len(body["entries"])
    assert all(e["action"].startswith("server.create") for e in body["entries"])


# ------------------------------------------------------------------ players


def test_offline_profile_cache_is_mode_scoped(tmp_path):
    """The same name has different UUIDs online vs offline; never reuse."""
    from nautilus.resources import players as svc

    class FakeServer:
        directory = tmp_path

    (tmp_path / "server.properties").write_text("online-mode=false\n", encoding="utf-8")
    svc._profiles.pop("offline:steve", None)
    svc._profiles["online:steve"] = {"uuid": "00000000-0000-0000-0000-000000000000", "source": "mojang"}

    import asyncio

    profile = asyncio.run(svc.resolve(FakeServer(), "steve"))
    assert profile["source"] == "offline"
    assert profile["uuid"] != "00000000-0000-0000-0000-000000000000"
    assert profile["uuid"] == svc.offline_uuid("steve")


# ---------------------------------------------------------- process helpers


def test_port_probe_sees_listening_socket():
    from nautilus.servers.manager import ManagerError, free_port, port_in_use

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        assert port_in_use(port) is True
        # A one-port range that is taken must report "no free port", not return
        # the busy port (the old SO_REUSEADDR probe did the latter on Windows).
        with pytest.raises(ManagerError):
            free_port(port, port + 1)


def test_windows_arg_splitter_roundtrip():
    import subprocess

    from nautilus.servers.manager import _split_windows

    for args in (
        ["-Dfoo=bar baz", "-Xmx2G", r"C:\a b\c"],
        ["--nogui"],
        ["-Dq=a\"b"],
    ):
        assert _split_windows(subprocess.list2cmdline(args)) == args
    assert _split_windows('--motd "hello world"') == ["--motd", "hello world"]


def test_cpu_sampler_uses_monotonic_clock(monkeypatch):
    """A wall-clock jump must not produce a negative or absurd CPU sample."""
    from nautilus.servers import manager as mgr

    rt = mgr.Runtime(server_id=9999)
    # Cumulative CPU was 0s at monotonic t=100s; it is 1s at t=102s.
    rt.cpu_prev = (100.0, 0.0)

    class FakeTimes:
        user = 1.0
        system = 0.0

    class FakeProc:
        def cpu_times(self):
            return FakeTimes()

        def memory_info(self):
            return type("M", (), {"rss": 1024 * 1024})()

        def num_threads(self):
            return 4

        def children(self, recursive=False):
            return []

    rt.ps = FakeProc()
    rt.proc = type("P", (), {"returncode": None, "pid": 1})()
    m = mgr.ServerManager()
    m._rt[9999] = rt
    monkeypatch.setattr(mgr.time, "monotonic", lambda: 102.0)
    monkeypatch.setattr(mgr.time, "time", lambda: 5_000_000.0)  # wild wall clock
    m.sample()
    point = rt.metrics[-1]
    assert point["cpu"] == pytest.approx(50.0)  # (1s CPU / 2s wall) * 100
    assert point["ts"] == 5_000_000.0  # display timestamp still wall-clock


# ------------------------------------------------------------ runtime lifecycle


async def test_forget_cleans_up_background_tasks():
    """Deleting a server must not leak its runtime or a sleeping restart timer."""
    from nautilus.servers import manager as mgr

    m = mgr.ServerManager()
    rt = m.get(4242)
    rt.autorestart = asyncio.create_task(asyncio.sleep(3600))
    rt.state = "crashed"

    m.forget(4242)

    assert not m.has(4242)
    await asyncio.sleep(0.05)
    assert rt.autorestart.cancelled()


async def test_stop_cancels_pending_autorestart(monkeypatch):
    """A queued crash-restart must not resurrect a server the admin stopped."""
    from nautilus.servers import manager as mgr

    monkeypatch.setattr(mgr, "IS_WINDOWS", False)  # skip taskkill on the fake pid

    class FakeProc:
        pid = 999_999
        returncode = None

        def kill(self) -> None:
            self.returncode = -9

    m = mgr.ServerManager()
    rt = m.get(4343)
    rt.proc = FakeProc()
    rt.state = "running"
    rt.autorestart = asyncio.create_task(asyncio.sleep(3600))

    await m.stop(types.SimpleNamespace(id=4343), force=True)

    assert rt.expected_stop is True
    assert rt.autorestart is None
    assert rt.state == "stopped"


async def test_crash_restart_backoff_resets_after_healthy_run():
    """Ten crashes spread over weeks must not disable auto-restart forever."""
    from nautilus.servers import manager as mgr

    class FakeProc:
        pid = 999_998
        returncode = None

        async def wait(self):
            self.returncode = 1
            return 1

    async def crash(server_id, *, uptime, restarts):
        m = mgr.ServerManager()
        rt = m.get(server_id)
        rt.proc = FakeProc()
        rt.state = "running"
        rt.restarts = restarts
        rt.started_at = time.time() - uptime
        await m._wait_exit(types.SimpleNamespace(id=server_id, auto_restart=True), rt)
        task, rt.autorestart = rt.autorestart, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        return rt, task is not None

    # Short-lived crash keeps climbing towards the cap.
    rt, scheduled = await crash(7001, uptime=5, restarts=9)
    assert rt.state == "crashed"
    assert rt.restarts == 10
    assert scheduled

    # A run that lasted ten minutes counts as healthy: counter starts over.
    rt, scheduled = await crash(7002, uptime=600, restarts=9)
    assert rt.restarts == 1
    assert scheduled

    # Cap reached: no restart is scheduled.
    rt, scheduled = await crash(7003, uptime=5, restarts=mgr.AUTO_RESTART_MAX)
    assert not scheduled
    assert rt.restarts == mgr.AUTO_RESTART_MAX


async def test_plugin_download_uses_servers_provision_module(tmp_path, monkeypatch):
    """install_project 曾因 `from .provision import download` 报 ModuleNotFoundError。"""
    from nautilus.resources import plugins
    from nautilus.servers import provision

    seen = {}

    async def fake_download(url, target, *, expected_sha256=None, expected_sha1=None, progress=None):
        seen.update({"url": url, "sha1": expected_sha1})
        return target

    monkeypatch.setattr(provision, "download", fake_download)
    artifact = plugins.Artifact(url="https://example.com/x.jar", filename="x.jar", sha1="abc123")
    target = tmp_path / "x.jar"
    out = await plugins._download_verified(artifact, target)

    assert out == target
    assert seen == {"url": "https://example.com/x.jar", "sha1": "abc123"}


async def test_plugin_download_rejects_missing_hash(tmp_path):
    from nautilus.resources import plugins

    artifact = plugins.Artifact(url="https://example.com/x.jar", filename="x.jar")
    try:
        await plugins._download_verified(artifact, tmp_path / "x.jar")
    except plugins.PluginError:
        pass
    else:
        raise AssertionError("没有哈希的下载源必须被拒绝")
