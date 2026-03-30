from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_current_actor
from app.auth import issue_access_token
from app.authz import Actor, can_write_store, require_store_write_access
from app.models import Base, Role, StoreConnection, Tenant, User, UserStoreAccess


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, class_=Session, autocommit=False, autoflush=False)()


def _seed(db: Session):
    tenant = Tenant(id="t1", name="Tenant One")
    owner = User(id="u_owner", tenant_id=tenant.id, email="owner@example.com", role=Role.owner)
    member = User(id="u_member", tenant_id=tenant.id, email="member@example.com", role=Role.member)
    read_only = User(id="u_ro", tenant_id=tenant.id, email="ro@example.com", role=Role.read_only)
    store = StoreConnection(
        id="s1",
        tenant_id=tenant.id,
        shop_domain="store.myshopify.com",
        access_token_enc="",
        scopes=[],
        token_source="oauth",
    )
    db.add_all([tenant, owner, member, read_only, store])
    db.add(UserStoreAccess(user_id=member.id, store_id=store.id, can_write=True))
    db.add(UserStoreAccess(user_id=read_only.id, store_id=store.id, can_write=False))
    db.commit()
    return owner, member, read_only, store


def test_read_only_cannot_confirm_write():
    db = _session()
    _, _, ro, store = _seed(db)
    actor = Actor(tenant_id="t1", user_id=ro.id, role=Role.read_only)
    assert can_write_store(db, actor, store.id) is False
    try:
        require_store_write_access(db, actor, store.id)
        assert False, "Expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 403


def test_member_write_access_is_store_scoped():
    db = _session()
    _, member, _, store = _seed(db)
    actor = Actor(tenant_id="t1", user_id=member.id, role=Role.member)
    assert can_write_store(db, actor, store.id) is True
    assert can_write_store(db, actor, "unknown-store") is False


def test_owner_allowed_for_store_writes():
    db = _session()
    owner, _, _, store = _seed(db)
    actor = Actor(tenant_id="t1", user_id=owner.id, role=Role.owner)
    assert can_write_store(db, actor, store.id) is True


def test_spoofed_headers_do_not_work_when_legacy_disabled(monkeypatch):
    db = _session()
    _seed(db)
    monkeypatch.setenv("AUTH_ALLOW_LEGACY_HEADERS", "false")
    try:
        get_current_actor(db=db, authorization=None, x_tenant_id="t1", x_user_id="u_owner")
        assert False, "Expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 401


def test_bearer_token_resolves_actor_even_with_legacy_disabled(monkeypatch):
    db = _session()
    _seed(db)
    monkeypatch.setenv("AUTH_ALLOW_LEGACY_HEADERS", "false")
    token = issue_access_token(tenant_id="t1", user_id="u_owner")
    actor = get_current_actor(db=db, authorization=f"Bearer {token}", x_tenant_id=None, x_user_id=None)
    assert actor.user_id == "u_owner"
    assert actor.tenant_id == "t1"
