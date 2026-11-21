"""Auth, RBAC and audit smoke tests."""

from __future__ import annotations

from .conftest import ADMIN


async def test_health_is_public(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


async def test_me_requires_session(client):
    r = await client.get("/api/auth/me")
    assert r.status_code == 401


async def test_login_rejects_wrong_password(client):
    r = await client.post("/api/auth/login", data={"username": "admin@example.com", "password": "wrong-password"})
    assert r.status_code == 400


async def test_login_and_me(admin):
    r = await admin.get("/api/auth/me")
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["role"] == "admin"
    assert body["user"]["username"] == "admin"
    assert any(p.startswith(("server:", "*:")) for p in body["permissions"])


async def test_cookie_is_httponly(admin):
    # the session cookie must never be readable from JS
    cookies = admin.cookies.jar
    names = {c.name for c in cookies}
    assert any("nautilus_session" in n for n in names)


async def test_rbac_matrix():
    from nautilus.authz import authz

    assert authz.can("admin", "server", "delete")
    assert authz.can("operator", "server", "control")
    assert not authz.can("operator", "server", "delete")
    assert authz.can("moderator", "server", "console")
    assert not authz.can("moderator", "server", "files")
    assert authz.can("viewer", "server", "read")
    assert not authz.can("viewer", "server", "console")
    assert not authz.can("nonexistent", "server", "read")


async def test_viewer_cannot_create_server(app):
    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        await c.post("/api/auth/login", data={"username": "admin@example.com", "password": ADMIN["password"]})
        created = await c.post(
            "/api/users",
            json={
                "username": "viewer1",
                "email": "viewer1@example.com",
                "password": "ViewerPass123",
                "role": "viewer",
            },
        )
        assert created.status_code == 201, created.text
        await c.post("/api/auth/logout")
        r = await c.post("/api/auth/login", data={"username": "viewer1@example.com", "password": "ViewerPass123"})
        assert r.status_code == 204
        r = await c.post("/api/servers", json={"name": "nope", "kind": "paper"})
        assert r.status_code == 403


async def test_user_crud(admin):
    r = await admin.post(
        "/api/users",
        json={"username": "op1", "email": "op1@example.com", "password": "OperatorPass1", "role": "operator"},
    )
    assert r.status_code == 201, r.text
    uid = r.json()["id"]

    r = await admin.patch(f"/api/users/{uid}", json={"role": "moderator"})
    assert r.status_code == 200
    assert r.json()["role"] == "moderator"

    r = await admin.post("/api/users", json={"username": "op1", "email": "dup@example.com", "password": "OperatorPass1"})
    assert r.status_code == 409

    r = await admin.delete(f"/api/users/{uid}")
    assert r.status_code == 204


async def test_audit_records_writes(admin):
    r = await admin.get("/api/audit")
    assert r.status_code == 200
    actions = [e["action"] for e in r.json()["entries"]]
    assert any(a.startswith("user.") for a in actions)


async def test_cross_origin_write_is_blocked(admin):
    r = await admin.post("/api/servers", json={"name": "csrf"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
