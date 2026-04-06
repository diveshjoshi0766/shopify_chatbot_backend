from __future__ import annotations

from fastapi import HTTPException

from app.api.deps import get_current_actor
from app.auth import issue_access_token
from app.authz import Actor, can_write_store, require_store_write_access
from app.models import Role, StoreStatus
from tests.mongo_helpers import make_test_repository


def _seed(db):
    db.insert_tenant(tenant_id="t1", name="Tenant One")
    db.insert_user(
        tenant_id="t1",
        email="owner@example.com",
        display_name=None,
        password_hash=None,
        role=Role.owner,
        user_id="u_owner",
    )
    db.insert_user(
        tenant_id="t1",
        email="member@example.com",
        display_name=None,
        password_hash=None,
        role=Role.member,
        user_id="u_member",
    )
    db.insert_user(
        tenant_id="t1",
        email="ro@example.com",
        display_name=None,
        password_hash=None,
        role=Role.read_only,
        user_id="u_ro",
    )
    db.insert_store_connection(
        tenant_id="t1",
        shop_domain="store.myshopify.com",
        access_token_enc="",
        scopes=[],
        status=StoreStatus.active,
        token_source="oauth",
        store_id="s1",
    )


def test_read_only_cannot_confirm_write():
    db = make_test_repository()
    _seed(db)
    actor = Actor(tenant_id="t1", user_id="u_ro", role=Role.read_only)
    assert can_write_store(db, actor, "s1") is False
    try:
        require_store_write_access(db, actor, "s1")
        assert False, "Expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 403


def test_member_write_access_all_tenant_stores():
    db = make_test_repository()
    _seed(db)
    actor = Actor(tenant_id="t1", user_id="u_member", role=Role.member)
    assert can_write_store(db, actor, "s1") is True
    assert can_write_store(db, actor, "unknown-store") is False


def test_owner_allowed_for_store_writes():
    db = make_test_repository()
    _seed(db)
    actor = Actor(tenant_id="t1", user_id="u_owner", role=Role.owner)
    assert can_write_store(db, actor, "s1") is True


def test_spoofed_headers_do_not_work_when_legacy_disabled(monkeypatch):
    db = make_test_repository()
    _seed(db)
    monkeypatch.setenv("AUTH_ALLOW_LEGACY_HEADERS", "false")
    try:
        get_current_actor(db=db, authorization=None, x_tenant_id="t1", x_user_id="u_owner")
        assert False, "Expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 401


def test_bearer_token_resolves_actor_even_with_legacy_disabled(monkeypatch):
    db = make_test_repository()
    _seed(db)
    monkeypatch.setenv("AUTH_ALLOW_LEGACY_HEADERS", "false")
    token = issue_access_token(tenant_id="t1", user_id="u_owner")
    actor = get_current_actor(db=db, authorization=f"Bearer {token}", x_tenant_id=None, x_user_id=None)
    assert actor.user_id == "u_owner"
    assert actor.tenant_id == "t1"
