"""End-to-end check: install, start, command and stop a real Minecraft server.

Run with:  python scripts/e2e_vanilla.py
Requires a working ``java`` (17+ for 1.20.4) and network access to Mojang.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import time
from pathlib import Path
from functools import partial

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DATA = ROOT / ".tmp" / "e2e-data"
VERSION = os.environ.get("E2E_VERSION", "1.20.4")


def _print(*args, **kwargs):
    print(*args, flush=True, **kwargs)


async def main() -> int:
    shutil.rmtree(DATA, ignore_errors=True)
    os.environ["NAUTILUS_DATA_DIR"] = str(DATA)
    os.environ["NAUTILUS_BOOTSTRAP_ADMIN_PASSWORD"] = "NautilusAdmin2026"
    os.environ["NAUTILUS_LOG_LEVEL"] = "info"
    os.environ["NAUTILUS_METRICS_INTERVAL"] = "2"

    import httpx

    from nautilus.main import create_app
    from nautilus.servers.manager import manager

    app = create_app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://e2e", timeout=120) as c:
            r = await c.post("/api/auth/login", data={"username": "admin@example.com", "password": "NautilusAdmin2026"})
            assert r.status_code == 204, f"login failed: {r.status_code} {r.text}"

            _print(f"[1/6] creating server (vanilla {VERSION}) ...")
            r = await c.post(
                "/api/servers",
                json={
                    "name": "e2e-vanilla",
                    "kind": "vanilla",
                    "version": VERSION,
                    "memory_mb": 1200,
                    "eula_accepted": True,
                    "install": True,
                    "auto_restart": False,
                },
            )
            assert r.status_code == 201, r.text
            sid = r.json()["id"]
            port = r.json()["port"]
            _print(f"      server id={sid} port={port}")

            _print("[2/6] waiting for the jar download ...")
            jar = DATA / "servers" / str(sid) / "server.jar"
            deadline = time.time() + 420
            while time.time() < deadline:
                if jar.exists() and jar.stat().st_size > 5_000_000:
                    break
                await asyncio.sleep(3)
            assert jar.exists(), "jar was not downloaded"
            _print(f"      jar ready: {jar.stat().st_size / 1048576:.1f} MB")

            _print("[3/6] starting ...")
            r = await c.post(f"/api/servers/{sid}/start")
            assert r.status_code == 200, r.text
            ready = False
            deadline = time.time() + 180
            while time.time() < deadline:
                state = manager.snapshot(sid)["state"]
                if state == "running":
                    ready = True
                    break
                if state == "crashed":
                    break
                await asyncio.sleep(2)
            snap = manager.snapshot(sid)
            _print(f"      state={snap['state']} pid={snap['pid']}")
            if not ready:
                logs = (DATA / "servers" / str(sid) / "logs" / "nautilus-console.log")
                _print("---- last console lines ----")
                _print("\n".join(logs.read_text(encoding="utf-8", errors="replace").splitlines()[-25:]))
                return 1

            _print("[4/6] sending console command via RCON ...")
            r = await c.post(f"/api/servers/{sid}/command", json={"command": "list"})
            _print(f"      rcon output: {r.text[:160]}")

            _print("[5/6] metrics / logs ...")
            await asyncio.sleep(4)
            m = (await c.get(f"/api/servers/{sid}/metrics")).json()
            assert m["points"], "no metric samples collected"
            _print(f"      samples={len(m['points'])} cpu={m['points'][-1]['cpu']} rss={m['points'][-1]['rss_mb']:.0f}MB")
            logs = (await c.get(f"/api/servers/{sid}/logs", params={"limit": 5})).json()["lines"]
            _print(f"      last log: {logs[-1]['line'][:110] if logs else 'n/a'}")

            _print("[6/6] graceful stop ...")
            r = await c.post(f"/api/servers/{sid}/stop")
            assert r.status_code == 200, r.text
            deadline = time.time() + 90
            while time.time() < deadline:
                if manager.snapshot(sid)["state"] == "stopped":
                    break
                await asyncio.sleep(2)
            final = manager.snapshot(sid)
            _print(f"      final state={final['state']} exit={final['last_exit_code']}")
            assert final["state"] == "stopped", final

    _print("\nE2E OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
