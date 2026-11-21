"""Test fixtures: run the real app against a throwaway data directory."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / ".tmp" / "test-data"

os.environ.setdefault("NAUTILUS_DATA_DIR", str(DATA))
os.environ["NAUTILUS_BOOTSTRAP_ADMIN_PASSWORD"] = "NautilusAdmin2026"
os.environ["NAUTILUS_BOOTSTRAP_ADMIN_USER"] = "admin"
os.environ["NAUTILUS_COOKIE_SECURE"] = "false"
os.environ["NAUTILUS_METRICS_INTERVAL"] = "60"
os.environ["NAUTILUS_LOG_LEVEL"] = "warning"

ADMIN = {"username": "admin", "password": "NautilusAdmin2026"}


@pytest.fixture(scope="session", autouse=True)
def _clean_data():
    if DATA.exists():
        shutil.rmtree(DATA, ignore_errors=True)
    DATA.mkdir(parents=True, exist_ok=True)
    yield


@pytest.fixture()
async def app():
    from nautilus.main import create_app

    application = create_app()
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture()
async def client(app):
    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.fixture()
async def admin(client):
    r = await client.post("/api/auth/login", data={"username": "admin@example.com", "password": ADMIN["password"]})
    assert r.status_code == 204, r.text
    return client
