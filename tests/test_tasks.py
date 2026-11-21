"""Scheduled tasks: cron validation, lifecycle, manual run and persistence."""

from __future__ import annotations

import pytest

from nautilus.servers import tasks as svc


async def _make_server(client, name="task-host"):
    r = await client.post("/api/servers", json={"name": name, "kind": "paper", "version": "1.21.4", "install": False})
    assert r.status_code == 201, r.text
    return r.json()


def test_cron_parser_accepts_five_fields():
    trigger = svc.make_trigger("0 4 * * *")
    assert trigger is not None
    assert svc.make_trigger("*/5  *  * * 1-5") is not None


@pytest.mark.parametrize("expr", ["", "0 4 * *", "61 * * * *", "every day"])
def test_cron_parser_rejects_garbage(expr):
    with pytest.raises(svc.TaskError):
        svc.make_trigger(expr)


async def test_task_lifecycle(admin):
    server = await _make_server(admin)
    sid = server["id"]

    r = await admin.post(
        f"/api/servers/{sid}/tasks",
        json={"name": "凌晨备份", "kind": "backup", "cron": "0 4 * * *", "payload": "world"},
    )
    assert r.status_code == 201, r.text
    task = r.json()
    assert task["enabled"] is True
    assert task["next_run"], "an enabled task must be armed with the scheduler"

    r = await admin.get(f"/api/servers/{sid}/tasks")
    payload = r.json()
    assert [t["id"] for t in payload["tasks"]] == [task["id"]]
    assert "backup" in payload["kinds"]

    r = await admin.patch(f"/api/servers/{sid}/tasks/{task['id']}", json={"enabled": False, "cron": "30 3 * * *"})
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is False
    assert r.json()["next_run"] is None

    r = await admin.patch(f"/api/servers/{sid}/tasks/{task['id']}", json={"cron": "not a cron"})
    assert r.status_code == 400

    r = await admin.delete(f"/api/servers/{sid}/tasks/{task['id']}")
    assert r.status_code == 204
    assert (await admin.get(f"/api/servers/{sid}/tasks")).json()["tasks"] == []

    await admin.delete(f"/api/servers/{sid}", params={"purge": True})


async def test_command_task_requires_payload(admin):
    server = await _make_server(admin, "task-empty")
    r = await admin.post(
        f"/api/servers/{server['id']}/tasks",
        json={"name": "空指令", "kind": "command", "cron": "0 4 * * *", "payload": ""},
    )
    assert r.status_code == 400
    await admin.delete(f"/api/servers/{server['id']}", params={"purge": True})


async def test_manual_run_records_failure_instead_of_raising(admin):
    server = await _make_server(admin, "task-run")
    r = await admin.post(
        f"/api/servers/{server['id']}/tasks",
        json={"name": "广播", "kind": "command", "cron": "0 4 * * *", "payload": "say hi"},
    )
    task = r.json()

    # Server is stopped: the run must be recorded as an error, not a 500.
    r = await admin.post(f"/api/servers/{server['id']}/tasks/{task['id']}/run")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "error"
    assert "未运行" in r.json()["result"]

    tasks = (await admin.get(f"/api/servers/{server['id']}/tasks")).json()["tasks"]
    assert tasks[0]["last_status"] == "error"
    assert tasks[0]["last_run"]

    await admin.delete(f"/api/servers/{server['id']}", params={"purge": True})


async def test_task_is_scoped_to_its_server(admin):
    a = await _make_server(admin, "task-a")
    b = await _make_server(admin, "task-b")
    task = (
        await admin.post(
            f"/api/servers/{a['id']}/tasks",
            json={"name": "仅 A", "kind": "restart", "cron": "0 4 * * *"},
        )
    ).json()

    r = await admin.delete(f"/api/servers/{b['id']}/tasks/{task['id']}")
    assert r.status_code == 404
    r = await admin.post(f"/api/servers/{b['id']}/tasks/{task['id']}/run")
    assert r.status_code == 404

    await admin.delete(f"/api/servers/{a['id']}", params={"purge": True})
    await admin.delete(f"/api/servers/{b['id']}", params={"purge": True})


def test_naive_db_timestamps_serialise_as_utc():
    """SQLite drops the offset; the API must not let browsers guess local time."""
    from datetime import datetime

    from nautilus.models import iso_utc

    assert iso_utc(datetime(2026, 11, 22, 3, 30)).endswith("+00:00")
    aware = iso_utc(datetime(2026, 11, 22, 3, 30).astimezone())
    assert aware is not None and aware.endswith(("+08:00", "+00:00"))
    assert iso_utc(None) is None
