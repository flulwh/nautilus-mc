"""WebSocket contract tests.

Two regressions are pinned here:

* ``JWTStrategy.read_token`` returns a *detached-candidate* ORM object, not an
  id. Feeding it back into ``session.get`` raised a ``ProgrammingError`` during
  the handshake and surfaced as a bare HTTP 500.
* ``Channel`` must be hashable (identity) so the bus can keep subscribers in a
  ``set``; a plain ``@dataclass`` sets ``__hash__ = None``.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from .conftest import ADMIN


def _login(client: TestClient) -> None:
    r = client.post(
        "/api/auth/login",
        data={"username": "admin@example.com", "password": ADMIN["password"]},
    )
    assert r.status_code == 204, r.text


def _new_server(client: TestClient) -> int:
    r = client.post(
        "/api/servers",
        json={
            "name": "ws-fixture",
            "kind": "vanilla",
            "version": "1.20.4",
            "port": 25599,
            "rcon_port": 25609,
            "memory_mb": 1024,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_console_stream_sends_initial_frames():
    from nautilus.main import create_app

    with TestClient(create_app()) as client:
        _login(client)
        server_id = _new_server(client)

        with client.websocket_connect(f"/ws/servers/{server_id}") as ws:
            state = ws.receive_json()
            history = ws.receive_json()
            metrics = ws.receive_json()
            tps = ws.receive_json()

        assert state["t"] == "state"
        assert state["id"] == server_id
        assert state["state"] == "stopped"
        assert history == {"t": "history", "lines": []}
        # The backfill is a distinct topic from the per-tick ``metrics`` sample;
        # merging them made the client treat the batch as a data point.
        assert metrics == {"t": "metrics_history", "points": []}
        assert tps == {"t": "tps_history", "points": []}


def test_console_stream_rejects_anonymous():
    from nautilus.main import create_app

    with TestClient(create_app()) as client, pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/servers/1") as ws:
            ws.receive_json()  # the server accepts-then-closes with 4401
    assert exc.value.code == 4401


def test_console_stream_404s_for_unknown_server():
    from nautilus.main import create_app

    with TestClient(create_app()) as client:
        _login(client)
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws/servers/99999") as ws:
                ws.receive_json()  # accepts-then-closes with 4404
    assert exc.value.code == 4404


def test_global_event_stream_opens():
    from nautilus.main import create_app

    with TestClient(create_app()) as client:
        _login(client)
        with client.websocket_connect("/ws/events") as ws:
            hello = ws.receive_json()
        assert hello["t"] == "hello"
        assert hello["connections"] >= 1
