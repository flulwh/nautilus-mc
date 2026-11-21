"""Shared FastAPI dependencies.

``current_user`` comes straight from fastapi-users (cookie transport); the
permission guard layers Casbin on top.
"""

from __future__ import annotations

from typing import Annotated, Any, Callable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.backend import current_user
from .authz import authz
from .db import get_session
from .models import User

SessionDep = Annotated[AsyncSession, Depends(get_session)]
UserDep = Annotated[User, Depends(current_user)]


class Forbidden(HTTPException):
    def __init__(self, detail: str = "权限不足", need: str = "") -> None:
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
        self.need = need


def client_ip(request: Request) -> str:
    """Peer address, already de-proxied by uvicorn when that is configured.

    ``uvicorn`` is started with ``proxy_headers=True`` and
    ``forwarded_allow_ips`` (see ``__main__.py``), so ``scope["client"]`` is
    rewritten from ``X-Forwarded-For`` *only* for trusted proxies. Reading the
    header here as well would undo that check: any direct client could spoof
    its audit-log address. Trust the transport, not the header.
    """
    return request.client.host if request.client else ""


def require(obj: str, act: str) -> Callable[..., Any]:
    """Route guard: ``Depends(require("server", "control"))``.

    Delegated to Casbin so role inheritance and wildcards behave exactly as
    configured in the policy table.
    """

    async def _guard(user: UserDep) -> Any:
        if not authz.can(user.role, obj, act):
            raise Forbidden(f"缺少权限 {obj}:{act}", need=f"{obj}:{act}")
        return user

    return _guard


def require_any(*pairs: tuple[str, str]) -> Callable[..., Any]:
    async def _guard(user: UserDep) -> Any:
        for obj, act in pairs:
            if authz.can(user.role, obj, act):
                return user
        raise Forbidden("权限不足", need="|".join(f"{o}:{a}" for o, a in pairs))

    return _guard
