"""Cron scheduler for server maintenance tasks.

APScheduler owns the timers (same library that drives metrics sampling): it
gives us coalescing, ``max_instances`` protection, misfire grace and a clean
shutdown without hand-rolling a loop. The jobs themselves are rows in
``scheduled_tasks``, so the schedule survives restarts and is editable from the
UI.

Kinds:
    restart  - graceful stop, then start (no-op start when already stopped)
    start    - start when stopped
    stop     - graceful stop
    backup   - whole-world archive, ``payload`` selects the scope
    command  - send ``payload`` to the console
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from ..db import SessionFactory
from ..events import bus, topic
from ..models import ScheduledTask, Server
from ..settings import settings
from .manager import manager

log = logging.getLogger("nautilus.tasks")

KINDS = {
    "restart": "重启服务器",
    "start": "启动服务器",
    "stop": "停止服务器",
    "kill": "强制结束进程",
    "backup": "创建备份",
    "command": "执行指令",
}

scheduler = AsyncIOScheduler(timezone=settings.timezone)


class TaskError(RuntimeError):
    pass


def make_trigger(cron: str) -> CronTrigger:
    """Parse a standard 5-field crontab expression, in the panel's timezone."""
    expression = " ".join((cron or "").split())
    if len(expression.split()) != 5:
        raise TaskError("cron 需要 5 个字段：分 时 日 月 周，例如 0 4 * * *")
    try:
        return CronTrigger.from_crontab(expression, timezone=settings.timezone)
    except (ValueError, TypeError) as exc:
        raise TaskError(f"cron 表达式无效：{exc}") from exc


def start() -> None:
    if not scheduler.running:
        scheduler.start()
        log.info("task scheduler started (timezone=%s)", settings.timezone)


def shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


def _job_id(task_id: int) -> str:
    return f"task:{task_id}"


async def refresh(task_id: int) -> None:
    """Re-arm (or disarm) one task after a DB change."""
    if not scheduler.running:
        return
    try:
        scheduler.remove_job(_job_id(task_id))
    except Exception:
        pass
    async with SessionFactory() as s:
        row = await s.get(ScheduledTask, task_id)
        if row is None or not row.enabled:
            return
        try:
            trigger = make_trigger(row.cron)
        except TaskError as exc:
            log.warning("task %s not scheduled: %s", task_id, exc)
            return
        scheduler.add_job(
            run_task,
            trigger,
            args=[task_id],
            id=_job_id(task_id),
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )


async def sync_all() -> None:
    """Rebuild every job from the database (called once at startup)."""
    if not scheduler.running:
        return
    for job in scheduler.get_jobs():
        if job.id.startswith("task:"):
            scheduler.remove_job(job.id)
    async with SessionFactory() as s:
        rows = (await s.execute(select(ScheduledTask).where(ScheduledTask.enabled.is_(True)))).scalars().all()
    for row in rows:
        try:
            scheduler.add_job(
                run_task,
                make_trigger(row.cron),
                args=[row.id],
                id=_job_id(row.id),
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=300,
            )
        except TaskError as exc:
            log.warning("task %s skipped: %s", row.id, exc)
    log.info("scheduled %d maintenance task(s)", len(rows))


def next_run(task_id: int) -> str | None:
    job = scheduler.get_job(_job_id(task_id)) if scheduler.running else None
    return job.next_run_time.isoformat() if job and job.next_run_time else None


async def _record_result(task_id: int, status: str, result: str) -> None:
    """Persist the outcome, tolerating a momentarily busy SQLite writer."""
    for attempt in range(3):
        try:
            async with SessionFactory() as s:
                row = await s.get(ScheduledTask, task_id)
                if row is None:
                    return
                row.last_run = datetime.now(timezone.utc)
                row.last_status = status
                row.last_result = result[:2000]
                s.add(row)
                await s.commit()
            return
        except Exception as exc:  # noqa: BLE001 - never lose the job result
            if attempt == 2:
                log.warning("could not persist result of task %s: %s", task_id, exc)
                return
            import asyncio

            await asyncio.sleep(0.2 * (attempt + 1))


async def run_task(task_id: int, *, source: str = "cron") -> dict:
    """Execute one task. Exceptions are captured into ``last_result``."""
    async with SessionFactory() as s:
        row = await s.get(ScheduledTask, task_id)
        if row is None:
            raise TaskError("任务不存在")
        server = await s.get(Server, row.server_id)
        if server is None:
            await _record_result(task_id, "error", "服务器已被删除")
            raise TaskError("服务器已被删除")
        kind, payload, name, server_id = row.kind, row.payload, row.name, row.server_id

    bus.publish(topic(server_id, "task"), {"task_id": task_id, "name": name, "status": "running", "source": source})
    log.info("running scheduled task %s (%s) for server %s", task_id, kind, server_id)
    result = ""
    status = "ok"
    try:
        if kind == "restart":
            rt = manager.get(server_id)
            if rt.alive:
                await manager.restart(server)
                result = "已重启"
            else:
                await manager.start(server)
                result = "服务器原本未运行，已启动"
        elif kind == "start":
            await manager.start(server)
            result = "已启动"
        elif kind == "stop":
            await manager.stop(server)
            result = "已停止"
        elif kind == "kill":
            if not manager.get(server_id).alive:
                result = "服务器未在运行"
            else:
                await manager.stop(server, force=True)
                result = "已强制结束"
        elif kind == "backup":
            import asyncio

            from ..resources import backups as backup_svc

            scope = (payload or "world").strip() or "world"
            meta = await asyncio.to_thread(backup_svc.create, server, scope, f"定时-{name}")
            result = f"{meta.name}（{meta.entries} 个文件，{meta.bytes // 1048576 or 1} MB）"
        elif kind == "command":
            if not payload.strip():
                raise TaskError("指令为空")
            output = await manager.send(server, payload)
            result = output.strip()[:500] or "已发送"
        else:
            raise TaskError(f"未知的任务类型：{kind}")
    except Exception as exc:  # noqa: BLE001 - persisted for the UI
        status = "error"
        result = str(exc)[:2000]
        log.warning("scheduled task %s failed: %s", task_id, exc)
    finally:
        await _record_result(task_id, status, result)
        bus.publish(
            topic(server_id, "task"),
            {"task_id": task_id, "name": name, "status": status, "result": result, "source": source},
        )
        if status != "ok":
            from .. import notify

            notify.emit("task.failed", server_id=server_id, detail={"name": name, "error": result})
    return {"task_id": task_id, "status": status, "result": result}
