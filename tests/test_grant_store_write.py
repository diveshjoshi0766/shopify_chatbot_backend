from __future__ import annotations

from fastapi import HTTPException

from app.api.routes_admin import GrantStoreWriteIn, grant_store_write
from app.authz import Actor
from app.models import Role, StoreStatus
from tests.mongo_helpers import make_test_repository


def test_upsert_user_store_access_updates_can_write():
    db = make_test_repository()
    db.insert_user_store_access(user_id="u1", store_id="s1", can_write=False)
    db.upsert_user_store_access(user_id="u1", store_id="s1", can_write=True)
    m = db.get_user_store_access("u1", "s1")
    assert m is not None
    assert m.can_write is True


def test_grant_store_write_all_stores():
    db = make_test_repository()
    db.insert_tenant(tenant_id="t1", name="T1")
    db.insert_user(
        tenant_id="t1",
        email="admin@x.com",
        display_name=None,
        password_hash="x",
        role=Role.admin,
        user_id="admin1",
    )
    db.insert_user(
        tenant_id="t1",
        email="mem@x.com",
        display_name=None,
        password_hash="x",
        role=Role.member,
        user_id="mem1",
    )
    db.insert_store_connection(
        tenant_id="t1",
        shop_domain="a.myshopify.com",
        access_token_enc="",
        scopes=[],
        status=StoreStatus.active,
        token_source="oauth",
        store_id="s1",
    )
    actor = Actor(tenant_id="t1", user_id="admin1", role=Role.admin)
    out = grant_store_write(GrantStoreWriteIn(user_id="mem1", store_ids=None), actor, db)
    assert out["ok"] is True
    assert out["store_ids"] == ["s1"]
    assert db.get_user_store_access("mem1", "s1") is not None
    assert db.get_user_store_access("mem1", "s1").can_write is True


def test_grant_store_write_rejects_wrong_tenant_user():
    db = make_test_repository()
    db.insert_tenant(tenant_id="t1", name="T1")
    db.insert_tenant(tenant_id="t2", name="T2")
    db.insert_user(
        tenant_id="t1",
        email="admin@x.com",
        display_name=None,
        password_hash="x",
        role=Role.admin,
        user_id="admin1",
    )
    db.insert_user(
        tenant_id="t2",
        email="other@x.com",
        display_name=None,
        password_hash="x",
        role=Role.member,
        user_id="other1",
    )
    actor = Actor(tenant_id="t1", user_id="admin1", role=Role.admin)
    try:
        grant_store_write(GrantStoreWriteIn(user_id="other1", store_ids=None), actor, db)
        assert False
    except HTTPException as e:
        assert e.status_code == 404
