"""Application factory.

Wiring only: routers, middleware, lifespan. All behaviour lives in the modules.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from .api import backups, configs, files, players, plugins, servers, system, tasks
from .auth import router as auth_router
from .auth.backend import auth_backend, fastapi_users, get_user_manager  # noqa: F401  (re-export)
from .authz import authz
from .db import SessionFactory, autostart_servers, init_db
from .events import bus
from .extensions import collected_routers, emit, load_extensions
from .servers import metrics as metrics_svc
from .servers import tasks as task_svc
from .servers.manager import manager
from .settings import settings
from .ws import router as ws_router

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)-22s %(message)s",
)
log = logging.getLogger("nautilus")

limiter = Limiter(key_func=get_remote_address, default_limits=["600/minute"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await authz.reload_from_db()
    load_extensions()
    metrics_svc.start()
    task_svc.start()
    await task_svc.sync_all()
    emit("nautilus_startup", app=app, bus=bus)

    for server in await autostart_servers():
        try:
            await manager.start(server)
            log.info("auto-started server %s", server.name)
        except Exception as exc:
            log.warning("auto-start failed for %s: %s", server.name, exc)

    log.info("Nautilus ready on http://%s:%s", settings.host, settings.port)
    try:
        yield
    finally:
        task_svc.shutdown()
        metrics_svc.shutdown()
        await manager.shutdown()
        from .resources import plugins as plugin_svc

        await plugin_svc.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Nautilus",
        version="0.1.0",
        description="High-performance, extensible Minecraft server panel",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    if settings.cors_origins:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        started = time.perf_counter()
        # Same-origin enforcement for cookie-authenticated mutations (CSRF).
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin:
                from urllib.parse import urlparse

                if urlparse(origin).netloc != request.headers.get("host"):
                    return JSONResponse({"detail": "跨站请求已被拒绝"}, status_code=status.HTTP_403_FORBIDDEN)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        if request.url.path.startswith("/api") or request.url.path.startswith("/ws"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["Server-Timing"] = f"app;dur={(time.perf_counter() - started) * 1000:.1f}"
        return response

    # --- API ---------------------------------------------------------
    api_prefix = "/api"
    app.include_router(auth_router.router, prefix=api_prefix)
    app.include_router(servers.router, prefix=api_prefix)
    app.include_router(files.router, prefix=api_prefix)
    app.include_router(configs.router, prefix=api_prefix)
    app.include_router(plugins.router, prefix=api_prefix)
    app.include_router(players.router, prefix=api_prefix)
    app.include_router(backups.router, prefix=api_prefix)
    app.include_router(tasks.router, prefix=api_prefix)
    app.include_router(system.router, prefix=api_prefix)
    app.include_router(system.audit_router, prefix=api_prefix)

    for index, router in enumerate(collected_routers()):
        app.include_router(router, prefix=f"{api_prefix}/ext/{index}")

    app.include_router(ws_router)

    @app.get("/api/health", tags=["system"])
    async def health() -> dict:
        return {"ok": True, "authz": authz.ready, "connections": bus.connections}

    # --- frontend ----------------------------------------------------
    dist = settings.webui_dir
    if settings.serve_frontend and (dist / "index.html").exists():
        assets = dist / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            if full_path.startswith(("api", "ws")):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            candidate = (dist / full_path).resolve()
            if full_path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app


app = create_app()
