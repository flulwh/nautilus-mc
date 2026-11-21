"""End-to-end check for the new Forge auto-install path.

Creates a Forge server through the real HTTP API, lets the background installer
run, then boots it and waits for the ready line. Prints a compact trace so a
failure is diagnosable from the tail alone.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("E2E_DATA", ROOT / ".tmp" / "e2e-forge"))
os.environ["NAUTILUS_DATA_DIR"] = str(DATA)
os.environ["NAUTILUS_BOOTSTRAP_ADMIN_PASSWORD"] = "NautilusAdmin2026"
os.environ["NAUTILUS_BOOTSTRAP_ADMIN_USER"] = "admin"
os.environ["NAUTILUS_METRICS_INTERVAL"] = "2"
os.environ["NAUTILUS_LOG_LEVEL"] = "info"

import httpx  # noqa: E402

sys.path.insert(0, str(ROOT))


async def main() -> int:
    from nautilus.main import create_app
    from nautilus.servers.manager import manager

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=60) as c:
            r = await c.post("/api/auth/signin", json={"identifier": "admin", "password": "NautilusAdmin2026"})
            assert r.status_code == 204, r.text

            r = await c.post(
                "/api/servers",
                json={
                    "name": "forge-e2e",
                    "kind": "forge",
                    "version": "1.20.4",
                    "memory_mb": 1536,
                    "min_memory_mb": 512,
                    "eula_accepted": True,
                    "install": False,
                },
            )
            assert r.status_code == 201, r.text
            sid = r.json()["id"]
            print(f"[1/5] server id={sid} port={r.json()['port']}", flush=True)

            r = await c.post(f"/api/servers/{sid}/install", json={"kind": "forge", "version": "1.20.4"})
            assert r.status_code == 200, r.text
            print("[2/5] installer started", flush=True)

            deadline = time.monotonic() + 25 * 60
            last = -2
            while time.monotonic() < deadline:
                await asyncio.sleep(3)
                s = (await c.get(f"/api/servers/{sid}")).json()
                pct = s["runtime"]["install_pct"]
                if pct != last:
                    print(f"      install {pct}%  jar={s['jar']}", flush=True)
                    last = pct
                if pct == 100 or pct < 0:
                    break
            else:
                print("FAIL: install timed out")
                return 1
            if pct < 0:
                print(f"FAIL: install errored (pct={pct})")
                return 1

            s = (await c.get(f"/api/servers/{sid}")).json()
            directory = Path(s["directory"])
            jar = s["jar"]
            print(f"[3/5] installed jar={jar} exists={(directory / jar).exists()}", flush=True)
            print(f"      libraries={len(list((directory / 'libraries').rglob('*.jar'))) if (directory / 'libraries').exists() else 0} jars", flush=True)

            r = await c.post(f"/api/servers/{sid}/start")
            if r.status_code != 200:
                print("FAIL: start:", r.status_code, r.text)
                return 1
            print("[4/5] start requested", flush=True)

            ready = False
            deadline = time.monotonic() + 8 * 60
            while time.monotonic() < deadline:
                await asyncio.sleep(4)
                rt = manager.get(sid)
                if rt.state == "running":
                    ready = True
                    break
                if rt.state in {"crashed", "stopped"}:
                    break
            logs = (await c.get(f"/api/servers/{sid}/logs?limit=25")).json()
            print("[5/5] state=%s ready=%s" % (manager.get(sid).state, ready), flush=True)
            for line in logs.get("lines", [])[-12:]:
                print("      |", str(line.get("line", ""))[:160], flush=True)

            await manager.stop(await _server(sid), force=False)
            print("E2E FORGE", "OK" if ready else "FAILED", flush=True)
            return 0 if ready else 1


async def _server(server_id: int):
    from nautilus.db import SessionFactory
    from nautilus.models import Server

    async with SessionFactory() as s:
        return await s.get(Server, server_id)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
