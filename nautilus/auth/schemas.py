"""Pydantic schemas for the fastapi-users stack."""

from __future__ import annotations

from fastapi_users import schemas
from pydantic import Field


class UserRead(schemas.BaseUser[int]):
    username: str
    role: str
    created_at: str | None = None
    last_login_at: str | None = None


class UserCreate(schemas.BaseUserCreate):
    username: str = Field(min_length=2, max_length=32, pattern=r"^[A-Za-z0-9_.\-]+$")
    role: str = "viewer"


class UserUpdate(schemas.BaseUserUpdate):
    username: str | None = Field(default=None, min_length=2, max_length=32, pattern=r"^[A-Za-z0-9_.\-]+$")
    role: str | None = None
