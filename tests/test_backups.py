"""Backup archives: contents, restore semantics, traversal guards, API wiring."""

from __future__ import annotations

import asyncio
import tarfile
from types import SimpleNamespace

import pytest

from nautilus.resources import backups as svc
from nautilus.settings import settings


async def _make_server(client, name="backup-host"):
    r = await client.post("/api/servers", json={"name": name, "kind": "paper", "version": "1.21.4", "install": False})
    assert r.status_code == 201, r.text
    return r.json()


def _stub(sid: int) -> SimpleNamespace:
    return SimpleNamespace(id=sid, directory=settings.servers_dir / str(sid))


async def test_world_archive_contents(admin):
    data = await _make_server(admin, "backup-world")
    sid = data["id"]
    root = settings.servers_dir / str(sid)
    (root / "world" / "region").mkdir(parents=True, exist_ok=True)
    (root / "world" / "level.dat").write_bytes(b"level-A")
    (root / "world" / "region" / "r.0.0.mca").write_bytes(b"chunk")
    (root / "world_nether").mkdir(exist_ok=True)
    (root / "world_nether" / "level.dat").write_bytes(b"nether")
    (root / "plugins").mkdir(exist_ok=True)
    (root / "plugins" / "a.jar").write_bytes(b"jar")
    (root / "logs").mkdir(exist_ok=True)
    (root / "logs" / "latest.log").write_text("noise", encoding="utf-8")
    (root / "server.properties").write_text("level-name=world\n", encoding="utf-8")

    meta = svc.create(_stub(sid), "world", "夜间")
    assert meta.entries >= 4
    assert meta.bytes > 0

    archive = svc.archive_path(_stub(sid), meta.name)
    with tarfile.open(archive) as tf:
        names = tf.getnames()
    assert "world/level.dat" in names
    assert "world_nether/level.dat" in names
    assert "server.properties" in names
    assert not any(n.startswith("logs/") for n in names)
    assert not any(n.startswith("plugins/") for n in names)

    await admin.delete(f"/api/servers/{sid}", params={"purge": True})


async def test_restore_replaces_world_and_keeps_a_safety_copy(admin):
    data = await _make_server(admin, "backup-restore")
    sid = data["id"]
    root = settings.servers_dir / str(sid)
    (root / "world").mkdir(parents=True, exist_ok=True)
    (root / "world" / "level.dat").write_bytes(b"original")
    (root / "server.properties").write_text("level-name=world\n", encoding="utf-8")

    meta = svc.create(_stub(sid), "world", "snapshot")
    (root / "world" / "level.dat").write_bytes(b"tampered")

    result = svc.restore(_stub(sid), meta.name)
    assert (root / "world" / "level.dat").read_bytes() == b"original"
    assert "world" in result["replaced"]
    assert result["safety_copy"]

    safety = root / result["safety_copy"] / "world" / "level.dat"
    assert safety.read_bytes() == b"tampered"

    await admin.delete(f"/api/servers/{sid}", params={"purge": True})


async def test_restore_refuses_while_running(admin):
    data = await _make_server(admin, "backup-running")
    sid = data["id"]
    (settings.servers_dir / str(sid) / "world").mkdir(parents=True, exist_ok=True)
    (settings.servers_dir / str(sid) / "world" / "level.dat").write_bytes(b"x")
    meta = svc.create(_stub(sid), "world")

    with pytest.raises(svc.BackupError, match="停止"):
        svc.restore(_stub(sid), meta.name, running=True)

    await admin.delete(f"/api/servers/{sid}", params={"purge": True})


async def test_archive_name_traversal_is_rejected(admin):
    data = await _make_server(admin, "backup-safe")
    sid = data["id"]
    for bad in ("../escape.tar.gz", "..\\escape.tar.gz", "a/b.tar.gz", "x.zip"):
        with pytest.raises(svc.BackupError):
            svc.archive_path(_stub(sid), bad)
    await admin.delete(f"/api/servers/{sid}", params={"purge": True})


async def test_backup_api_roundtrip(admin):
    data = await _make_server(admin, "backup-api")
    sid = data["id"]
    root = settings.servers_dir / str(sid)
    (root / "world").mkdir(parents=True, exist_ok=True)
    (root / "world" / "level.dat").write_bytes(b"x" * 512)

    r = await admin.post(f"/api/servers/{sid}/backups", json={"scope": "world", "label": "api"})
    assert r.status_code == 202, r.text

    archive = None
    for _ in range(60):
        await asyncio.sleep(0.05)
        payload = (await admin.get(f"/api/servers/{sid}/backups")).json()
        if payload["archives"]:
            archive = payload["archives"][0]
            break
    assert archive is not None, "backup never appeared"
    assert archive["scope"] == "world"
    assert archive["label"] == "api"

    # Archive files are hidden from listing by name only; download must stream.
    r = await admin.get(f"/api/servers/{sid}/backups/{archive['name']}/download")
    assert r.status_code == 200
    assert len(r.content) == archive["bytes"]

    r = await admin.delete(f"/api/servers/{sid}/backups/{archive['name']}")
    assert r.status_code == 204
    assert (await admin.get(f"/api/servers/{sid}/backups")).json()["archives"] == []

    await admin.delete(f"/api/servers/{sid}", params={"purge": True})


async def test_invalid_scope_is_rejected(admin):
    data = await _make_server(admin, "backup-scope")
    r = await admin.post(f"/api/servers/{data['id']}/backups", json={"scope": "everything"})
    assert r.status_code == 422
    await admin.delete(f"/api/servers/{data['id']}", params={"purge": True})
