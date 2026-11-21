"""fastapi-users integration: user database adapter, manager, auth backend."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users import BaseUserManager, FastAPIUsers, models
from fastapi_users.authentication import AuthenticationBackend, CookieTransport, JWTStrategy
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.exceptions import InvalidPasswordException, UserNotExists
from sqlalchemy.ext.asyncio import AsyncSession

from ..authz import ROLES
from ..db import get_session
from ..models import User
from ..settings import settings

log = logging.getLogger("nautilus.auth")

# --------------------------------------------------------------------- storage


async def get_user_db(session: Annotated[AsyncSession, Depends(get_session)]) -> AsyncIterator[SQLAlchemyUserDatabase]:
    yield SQLAlchemyUserDatabase(session, User)


# --------------------------------------------------------------------- manager


#: email -> (failures, locked_until). Process-local on purpose: the panel runs
#: as a single worker (runtime state is in-process), so this is sufficient.
_ATTEMPTS: dict[str, tuple[int, float]] = {}
MAX_ATTEMPTS = 5
LOCK_SECONDS = 15 * 60


class UserManager(BaseUserManager[User, int]):
    reset_password_token_secret = settings.secret_key
    verification_token_secret = settings.secret_key

    def __init__(self, user_db: SQLAlchemyUserDatabase) -> None:
        super().__init__(user_db)

    async def authenticate(self, credentials: OAuth2PasswordRequestForm) -> User | None:
        """Password login with a per-identifier lockout.

        fastapi-users' routers treat a ``None`` return as "bad credentials" and
        raise the 400 themselves; raising ``UserNotExists`` here would escape
        their error handling and surface as a 500. A locked account therefore
        also returns ``None``, which keeps locked and wrong-password
        indistinguishable (no account enumeration).
        """
        key = credentials.username.strip().lower()
        failures, locked_until = _ATTEMPTS.get(key, (0, 0.0))
        if locked_until > time.time():
            return None
        try:
            user = await super().authenticate(credentials)
        except UserNotExists:
            user = None
        if user is None:
            failures += 1
            if failures >= MAX_ATTEMPTS:
                _ATTEMPTS[key] = (0, time.time() + LOCK_SECONDS)
                log.warning("login locked for %s after %d failures", key, failures)
            else:
                _ATTEMPTS[key] = (failures, 0.0)
            return None
        _ATTEMPTS.pop(key, None)
        return user

    def parse_id(self, value: object) -> int:
        """Integer primary keys need an explicit parser in fastapi-users."""
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            from fastapi_users.exceptions import InvalidID

            raise InvalidID() from exc

    async def validate_password(self, password: str, user: models.UP) -> None:
        if len(password) < 10:
            raise InvalidPasswordException(reason="密码至少需要 10 个字符")
        if password.isdigit() or password.isalpha():
            raise InvalidPasswordException(reason="密码需要同时包含字母与数字")

    async def on_after_login(
        self,
        user: User,
        request: Request | None = None,
        response=None,
    ) -> None:
        user.last_login_at = datetime.now(timezone.utc)
        await self.user_db.update(user, {"last_login_at": user.last_login_at})

    async def on_after_register(self, user: User, request: Request | None = None) -> None:
        # Keep fastapi-users' ``is_superuser`` aligned with our RBAC role so the
        # library-provided superuser endpoints work without extra glue.
        user.is_superuser = user.role == "admin"
        await self.user_db.update(user, {"is_superuser": user.is_superuser})


async def get_user_manager(
    user_db: Annotated[SQLAlchemyUserDatabase, Depends(get_user_db)],
) -> AsyncIterator[UserManager]:
    yield UserManager(user_db)


# --------------------------------------------------------------------- backend


cookie_transport = CookieTransport(
    cookie_name=settings.cookie_name,
    cookie_max_age=settings.token_ttl_minutes * 60,
    cookie_secure=settings.cookie_secure,
    cookie_httponly=True,
    cookie_samesite="lax",
    cookie_path="/",
)


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(secret=settings.secret_key, lifetime_seconds=settings.token_ttl_minutes * 60)


auth_backend = AuthenticationBackend(
    name="cookie",
    transport=cookie_transport,
    get_strategy=get_jwt_strategy,
)

fastapi_users = FastAPIUsers[User, int](get_user_manager, [auth_backend])

current_user = fastapi_users.current_user(active=True)
optional_user = fastapi_users.current_user(active=True, optional=True)


def validate_role(role: str) -> str:
    if role not in ROLES:
        raise ValueError(f"未知角色: {role}")
    return role
