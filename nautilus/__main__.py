"""``python -m nautilus`` entrypoint."""

from __future__ import annotations

import uvicorn

from .settings import settings


def main() -> None:
    uvicorn.run(
        "nautilus.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        workers=1,  # runtime state lives in-process; never scale workers
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
        root_path=settings.root_path,
    )


if __name__ == "__main__":
    main()
