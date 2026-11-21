"""Extension system built on pluggy (the pytest plugin engine).

Third-party packages register themselves through the ``nautilus.extensions``
entry-point group::

    [project.entry-points."nautilus.extensions"]
    my_ext = "my_ext.plugin"

A failing extension can never take the panel down: every hook call is wrapped
and only logged.
"""

from __future__ import annotations

import inspect
import logging
from importlib.metadata import entry_points

import pluggy

log = logging.getLogger("nautilus.extensions")

PROJECT = "nautilus"
hookspec = pluggy.HookspecMarker(PROJECT)
hookimpl = pluggy.HookimplMarker(PROJECT)


class NautilusSpec:
    """Hooks an extension may implement."""

    @hookspec
    def nautilus_startup(self, app, bus) -> None:
        """Called once after the app is built (async implementations allowed)."""

    @hookspec
    def nautilus_routes(self) -> list:
        """Return a list of ``APIRouter`` objects to mount under ``/api/ext``."""

    @hookspec
    def server_starting(self, server) -> None:
        ...

    @hookspec
    def server_started(self, server) -> None:
        ...

    @hookspec
    def server_exited(self, server, code: int | None) -> None:
        ...

    @hookspec
    def console_line(self, server_id: int, line: str, level: str) -> None:
        ...


pm = pluggy.PluginManager(PROJECT)
pm.add_hookspecs(NautilusSpec)


def load_extensions() -> list[str]:
    loaded: list[str] = []
    try:
        eps = entry_points(group=f"{PROJECT}.extensions")
    except Exception:  # pragma: no cover - very old importlib.metadata
        return loaded
    for ep in eps:
        try:
            plugin = ep.load()
            pm.register(plugin, name=ep.name)
            loaded.append(ep.name)
        except Exception:
            log.exception("failed to load extension %s", ep.name)
    if loaded:
        log.info("extensions loaded: %s", ", ".join(loaded))
    return loaded


def emit(hook_name: str, **kwargs) -> None:
    """Fire a hook, tolerating sync/async implementations and exceptions."""
    hook = getattr(pm.hook, hook_name)
    try:
        results = hook(**kwargs)
    except Exception:
        log.exception("hook %s failed", hook_name)
        return
    for result in results or []:
        if inspect.isawaitable(result):
            import asyncio

            try:
                asyncio.get_running_loop().create_task(result)
            except RuntimeError:  # pragma: no cover
                pass


def collected_routers() -> list:
    routers = []
    for result in pm.hook.nautilus_routes() or []:
        if isinstance(result, list):
            routers.extend(result)
        elif result is not None:
            routers.append(result)
    return routers
