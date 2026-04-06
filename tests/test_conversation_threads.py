from __future__ import annotations

from fastapi import HTTPException

from app.api.routes_chat import _ensure_conversation
from app.authz import Actor
from app.models import Role, StoreStatus
from tests.mongo_helpers import make_test_repository


def _seed(db):
    db.insert_tenant(tenant_id="t1", name="Tenant One")
    db.insert_user(
        tenant_id="t1",
        email="a@example.com",
        display_name=None,
        password_hash=None,
        role=Role.member,
        user_id="u_a",
    )
    db.insert_user(
        tenant_id="t1",
        email="b@example.com",
        display_name=None,
        password_hash=None,
        role=Role.member,
        user_id="u_b",
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


def test_ensure_conversation_creates_row_for_new_client_id():
    db = make_test_repository()
    _seed(db)
    actor = Actor(tenant_id="t1", user_id="u_a", role=Role.member)
    conv = _ensure_conversation(db, actor, "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    assert conv.id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert conv.tenant_id == "t1"
    assert conv.user_id == "u_a"


def test_ensure_conversation_rejects_other_users_conversation():
    db = make_test_repository()
    _seed(db)
    db.insert_conversation(
        conversation_id="conv-owned-by-b",
        tenant_id="t1",
        user_id="u_b",
        title="x",
    )
    actor = Actor(tenant_id="t1", user_id="u_a", role=Role.member)
    try:
        _ensure_conversation(db, actor, "conv-owned-by-b")
        assert False, "Expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 404


def test_pending_action_stores_conversation_link():
    db = make_test_repository()
    _seed(db)
    db.insert_conversation(conversation_id="conv-1", tenant_id="t1", user_id="u_a", title="t")
    pa = db.insert_pending_action(
        tenant_id="t1",
        user_id="u_a",
        conversation_id="conv-1",
        store_ids=["s1"],
        action_type="noop",
        tool_payload={},
        summary="s",
        pending_id="pa-1",
    )
    loaded = db.get_pending_action("pa-1")
    assert loaded is not None
    assert loaded.conversation_id == "conv-1"
