"""Personal API tokens: mint, list, revoke.

A token belongs to the user who created it and inherits that user's role at
request time (the role is read from the users table on every call), so
demoting a user immediately downgrades every token they ever issued.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..audit import record
from ..auth import tokens
from ..deps import SessionDep, UserDep, client_ip
from ..models import ApiToken

router = APIRouter(prefix="/tokens", tags=["tokens"])


class TokenCreate(BaseModel):
    name: str = Field(default="", max_length=60)
    #: ``0`` means "never expires". Anything else is clamped to ten years.
    days: int = Field(default=90, ge=0, le=3650)


@router.get("")
async def list_tokens(session: SessionDep, user: UserDep) -> dict:
    rows = (
        (await session.execute(select(ApiToken).where(ApiToken.user_id == user.id).order_by(ApiToken.id.desc())))
        .scalars()
        .all()
    )
    return {"tokens": [r.public() for r in rows], "prefix": tokens.PREFIX}


@router.post("", status_code=201)
async def create_token(
    payload: TokenCreate,
    request: Request,
    session: SessionDep,
    user: UserDep,
) -> dict:
    raw, token = tokens.mint(payload.name or "automation", payload.days or None)
    token.user_id = user.id
    session.add(token)
    await session.commit()
    await session.refresh(token)
    await record(
        session,
        action="token.create",
        actor=user,
        target=token.name,
        detail={"id": token.id, "days": payload.days},
        ip=client_ip(request),
    )
    # The plain value is returned exactly once and never persisted.
    return {"token": raw, "meta": token.public()}


@router.delete("/{token_id}", status_code=204)
async def revoke_token(
    token_id: int,
    request: Request,
    session: SessionDep,
    user: UserDep,
) -> None:
    token = await session.get(ApiToken, token_id)
    if token is None or token.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "令牌不存在")
    await session.delete(token)
    await record(session, action="token.revoke", actor=user, target=token.name, detail={"id": token_id}, ip=client_ip(request))
