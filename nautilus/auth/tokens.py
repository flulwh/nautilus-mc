"""API token minting and verification.

The panel's UI authenticates with an HttpOnly cookie, which is the right
default but useless for a script. Tokens follow the same shape as
GitHub/Stripe keys: a recognisable prefix, a random body, and only a hash at
rest. ``Authorization: Bearer nlt_...`` is accepted anywhere a cookie is.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ..db import SessionFactory
from ..models import ApiToken, User

PREFIX = "nlt_"
_RANDOM_BYTES = 32


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def mint(name: str, days: int | None = None) -> tuple[str, ApiToken]:
    raw = PREFIX + secrets.token_urlsafe(_RANDOM_BYTES)
    now = datetime.now(timezone.utc)
    token = ApiToken(
        name=(name or "token")[:60],
        prefix=PREFIX,
        token_hash=hash_token(raw),
        created_at=now,
        expires_at=now + timedelta(days=days) if days else None,
    )
    return raw, token


async def authenticate(raw: str) -> User | None:
    """Resolve a bearer token to an active user, or ``None``."""
    if not raw.startswith(PREFIX):
        return None
    digest = hash_token(raw.strip())
    async with SessionFactory() as s:
        token = (await s.execute(select(ApiToken).where(ApiToken.token_hash == digest))).scalar_one_or_none()
        if token is None:
            return None
        now = datetime.now(timezone.utc)
        expires = token.expires_at
        if expires is not None and expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires is not None and expires <= now:
            return None
        user = await s.get(User, token.user_id)
        if user is None or not user.is_active:
            return None
        # ``last_used_at`` is informational; writing it on every request would
        # turn a read-only poll into a write amplification problem.
        last = token.last_used_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if last is None or (now - last) > timedelta(minutes=1):
            token.last_used_at = now
            s.add(token)
            await s.commit()
    return user
