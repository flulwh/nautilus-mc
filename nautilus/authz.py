"""Authorization built on Casbin's RBAC model.

Policies live in the database (``policies`` table) and are materialised into a
model + policy file inside the data directory, then loaded into a pycasbin
``Enforcer``. Keeping the Casbin model as the single source of truth means role
inheritance (``g`` rules) and wildcard matching behave exactly as documented
upstream instead of being re-invented.

Fail-closed: if the enforcer could not be built, every check returns ``False``.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import casbin
from sqlalchemy import select

from .models import Policy
from .settings import settings

log = logging.getLogger("nautilus.authz")

MODEL_CONF = """
[request_definition]
r = sub, obj, act

[policy_definition]
p = sub, obj, act

[role_definition]
g = _, _

[policy_effect]
e = some(where (p.eft == allow))

[matchers]
m = g(r.sub, p.sub) && keyMatch(r.obj, p.obj) && regexMatch(r.act, p.act)
"""

ROLES = ["admin", "operator", "moderator", "viewer"]

#: Objects used by the API layer. ``*`` is only meaningful in policies.
OBJECTS = ["server", "users", "audit", "system"]

ACTIONS = [
    "read",
    "create",
    "delete",
    "control",
    "console",
    "files",
    "config",
    "plugins",
    "install",
    "backup",
    "players",
    "tasks",
    "manage",
]


class Authorizer:
    def __init__(self) -> None:
        self._enforcer: casbin.Enforcer | None = None
        self._lock = threading.Lock()
        self._roles: dict[str, list[str]] = {}

    # ------------------------------------------------------------------ load
    def load(self, policies: list[Policy]) -> None:
        base: Path = settings.data_dir
        base.mkdir(parents=True, exist_ok=True)
        model_path = base / "rbac_model.conf"
        policy_path = base / "rbac_policy.csv"
        model_path.write_text(MODEL_CONF.strip() + "\n", encoding="utf-8")
        lines = [p.line() for p in policies if p.v0]
        policy_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        with self._lock:
            try:
                enf = casbin.Enforcer(str(model_path), str(policy_path))
                self._enforcer = enf
                self._roles = {r: self._policies_of(r, policies) for r in ROLES}
                log.info("loaded %d RBAC rules", len(lines))
            except Exception:  # pragma: no cover - malformed policy file
                log.exception("failed to build casbin enforcer; access will be denied")
                self._enforcer = None

    @staticmethod
    def _policies_of(role: str, policies: list[Policy]) -> list[str]:
        return [f"{p.v1}:{p.v2}" for p in policies if p.ptype == "p" and p.v0 == role]

    async def reload_from_db(self) -> None:
        from .db import SessionFactory

        async with SessionFactory() as s:
            rows = (await s.execute(select(Policy))).scalars().all()
        self.load(list(rows))

    # ----------------------------------------------------------------- check
    def can(self, role: str, obj: str, act: str) -> bool:
        enf = self._enforcer
        if enf is None:
            return False
        try:
            return bool(enf.enforce(role, obj, act))
        except Exception:  # pragma: no cover
            log.exception("enforce failed for %s %s %s", role, obj, act)
            return False

    def permissions(self, role: str) -> list[str]:
        return list(self._roles.get(role, []))

    @property
    def ready(self) -> bool:
        return self._enforcer is not None


authz = Authorizer()
