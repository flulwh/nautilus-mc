"""Periodic sampling driven by APScheduler.

APScheduler owns the timer instead of a hand-rolled ``while True: sleep()``
loop: it gives us coalescing, max_instances protection and graceful shutdown
for free.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..settings import settings
from .manager import manager

log = logging.getLogger("nautilus.metrics")

scheduler = AsyncIOScheduler(timezone="UTC")


def start() -> None:
    if scheduler.running:
        return
    scheduler.add_job(
        manager.sample,
        IntervalTrigger(seconds=settings.metrics_interval),
        id="metrics",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _prune,
        IntervalTrigger(minutes=30),
        id="prune",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.start()
    log.info("metrics scheduler started (%ss interval)", settings.metrics_interval)


def shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


def _prune() -> None:
    """Trim in-memory metric history for servers that have been down a while."""
    for rt in manager._rt.values():  # noqa: SLF001 - internal by design
        if not rt.alive and len(rt.metrics) > settings.metrics_points:
            while len(rt.metrics) > settings.metrics_points:
                rt.metrics.popleft()
