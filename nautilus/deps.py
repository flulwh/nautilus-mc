"""Shared FastAPI dependencies.

``current_user`` comes straight from fastapi-users (cookie transport); the
permission guard layers Casbin on top.
"""

from __future__ import annotations

from typing import Annotated, Any, Callable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import tokens
from .auth.backend import optional_user
from .authz import authz
from .db import get_session
from .models import User

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def identity(request: Request, user: User | None = Depends(optional_user)) -> User:
    """Authenticate a request by bearer token **or** session cookie.

    Both routes end at the same ``User`` object, so every existing permission
    guard keeps working unchanged: a token gets exactly the role of the user
    who minted it, no more.
    """
    header = request.headers.get("authorization", "")
    if header[:7].lower() == "bearer ":
        raw = header[7:].strip()
        if raw.startswith(tokens.PREFIX):
            token_user = await tokens.authenticate(raw)
            if token_user is not None:
                return token_user
            # An explicit credential that does not verify must not silently fall
            # back to a cookie: that would hide an expired token behind a stale
            # browser session and make the failure impossible to diagnose.
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "API 令牌无效或已过期",
                headers={"WWW-Authenticate": "Bearer"},
            )
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


UserDep = Annotated[User, Depends(identity)]


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
