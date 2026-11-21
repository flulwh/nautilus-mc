"""Authentication & user-management routes.

Login/logout come from fastapi-users' own router (cookie transport, JWT
strategy, constant-time password verification). Only the panel-specific
endpoints are added here: ``/auth/me`` and admin user CRUD.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from ..audit import record
from ..authz import ROLES, authz
from ..db import SessionFactory
from ..deps import SessionDep, UserDep, client_ip, require
from ..models import AuditLog, User
from .backend import auth_backend, cookie_transport, fastapi_users, get_jwt_strategy, get_user_manager, validate_role
from .schemas import UserCreate, UserUpdate

router = APIRouter(tags=["auth"])

# fastapi-users: POST /auth/login (form-encoded), POST /auth/logout
router.include_router(fastapi_users.get_auth_router(auth_backend), prefix="/auth")


@router.post("/auth/signin")
async def signin(
    payload: dict,
    request: Request,
    response: Response,
    session: SessionDep,
    user_manager=Depends(get_user_manager),
) -> Response:
    """Cookie login accepting either an email or a username.

    Delegates credential checking to fastapi-users' manager (constant-time
    verification + lockout) and only maps the identifier first.
    """
    from fastapi.security import OAuth2PasswordRequestForm

    identifier = str(payload.get("identifier", "")).strip()
    password = str(payload.get("password", ""))
    if not identifier or not password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请填写账号与密码")

    email = identifier
    if "@" not in identifier:
        row = (await session.execute(select(User).where(User.username == identifier))).scalars().first()
        email = row.email if row else f"{identifier}@invalid"

    credentials = OAuth2PasswordRequestForm(username=email, password=password)
    try:
        user = await user_manager.authenticate(credentials)
    except Exception:
        user = None
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "账号或密码不正确")

    token = await get_jwt_strategy().write_token(user)
    await user_manager.on_after_login(user, None, None)
    await record(session, action="auth.login", actor=user, target=user.username, ip=client_ip(request))
    return await cookie_transport.get_login_response(token)


@router.get("/auth/me")
async def me(user: UserDep) -> dict:
    return {
        "user": user.public(),
        "permissions": authz.permissions(user.role),
        "roles": ROLES,
    }


@router.post("/auth/password")
async def change_password(
    payload: dict,
    request: Request,
    user: UserDep,
    session: SessionDep,
    user_manager=Depends(get_user_manager),
) -> dict:
    current = payload.get("current_password", "")
    new = payload.get("new_password", "")
    verified, updated_hash = user_manager.password_helper.verify_and_update(current, user.hashed_password)
    if not verified:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "当前密码不正确")
    try:
        await user_manager.validate_password(new, user)
    except Exception as exc:  # InvalidPasswordException carries a readable reason
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(getattr(exc, "reason", exc))) from None
    user.hashed_password = user_manager.password_helper.hash(new)
    session.add(user)
    await record(session, action="user.password", actor=user, target=user.username, ip=client_ip(request))
    return {"ok": True}


# --------------------------------------------------------------- admin: users

admin = APIRouter(prefix="/users", tags=["users"], dependencies=[Depends(require("users", "manage"))])


@admin.get("")
async def list_users(session: SessionDep) -> list[dict]:
    rows = (await session.execute(select(User).order_by(User.id))).scalars().all()
    return [u.public() for u in rows]


@admin.post("", status_code=201)
async def create_user(
    payload: UserCreate,
    request: Request,
    actor: UserDep,
    session: SessionDep,
    user_manager=Depends(get_user_manager),
) -> dict:
    validate_role(payload.role)
    try:
        created = await user_manager.create(payload, safe=False, request=request)
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "用户名或邮箱已存在") from None
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(getattr(exc, "reason", exc))) from None
    created.role = payload.role
    created.is_superuser = payload.role == "admin"
    session.add(created)
    await record(
        session,
        action="user.create",
        actor=actor,
        target=created.username,
        detail={"role": created.role},
        ip=client_ip(request),
    )
    return created.public()


@admin.patch("/{user_id}")
async def update_user(
    user_id: int,
    payload: UserUpdate,
    request: Request,
    actor: UserDep,
    session: SessionDep,
) -> dict:
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    data = payload.model_dump(exclude_unset=True, exclude={"password"})
    if "role" in data and data["role"]:
        validate_role(data["role"])
        if target.id == actor.id and data["role"] != "admin":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "不能降低自己的权限")
        if target.role == "admin" and data["role"] != "admin":
            if await count_admins() <= 1:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "至少需要保留一个管理员")
        target.is_superuser = data["role"] == "admin"
    for key, value in data.items():
        if value is not None:
            setattr(target, key, value)
    if payload.password:
        from fastapi_users.password import PasswordHelper

        if len(payload.password) < 10:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "密码至少需要 10 个字符")
        target.hashed_password = PasswordHelper().hash(payload.password)
    session.add(target)
    await record(
        session,
        action="user.update",
        actor=actor,
        target=target.username,
        detail={k: str(v) for k, v in data.items()},
        ip=client_ip(request),
    )
    return target.public()


@admin.delete("/{user_id}", status_code=204)
async def delete_user(user_id: int, request: Request, actor: UserDep, session: SessionDep) -> None:
    if user_id == actor.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "不能删除当前登录的账号")
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    if target.role == "admin" and await count_admins() <= 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "至少需要保留一个管理员")
    # ``audit_logs.actor_id`` references ``users.id`` and the audit trail must
    # outlive the account. Detach the rows first, otherwise SQLite's foreign-key
    # check turns every user deletion into a 500.
    await session.execute(update(AuditLog).where(AuditLog.actor_id == user_id).values(actor_id=None))
    await session.delete(target)
    await record(session, action="user.delete", actor=actor, target=target.username, ip=client_ip(request))


@admin.get("/{user_id}/sessions")
async def user_sessions(user_id: int) -> dict:
    """JWT sessions are stateless; surfaced so the UI can explain the model."""
    return {"stateless": True, "note": "JWT 无状态会话，吊销通过禁用账号或轮换密钥完成"}


async def count_admins() -> int:
    async with SessionFactory() as s:
        return int(await s.scalar(select(func.count()).select_from(User).where(User.role == "admin")) or 0)


def now() -> datetime:
    return datetime.now(timezone.utc)

# Mount the admin user-management router onto the same prefix.
router.include_router(admin)
