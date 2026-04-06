from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.routes_auth import RegisterRequest, register
from app.authz import Actor, can_write_store
from app.models import Role, StoreStatus
from tests.mongo_helpers import make_test_repository


@pytest.fixture
def db():
    return make_test_repository()


def test_register_admin_mode_success(monkeypatch, db):
    monkeypatch.setenv("AUTH_ADMIN_REGISTER_EMAIL", "admin@dyspensr.com")
    monkeypatch.setenv("AUTH_ADMIN_REGISTER_PASSWORD", "123456")
    monkeypatch.delenv("AUTH_REGISTRATION_PASSWORD", raising=False)

    db.insert_tenant(tenant_id="t1", name="T1")

    body = RegisterRequest(
        email="newuser@example.com",
        password="userpass12",
        admin_email="admin@dyspensr.com",
        admin_password="123456",
        access="read",
    )
    out = register(body, db)
    assert out["ok"] is True
    assert out["access"] == "read"
    u = db.get_user(out["user_id"])
    assert u is not None
    assert u.email == "newuser@example.com"
    assert u.role == Role.read_only


def test_register_write_without_store_ids_grants_all_tenant_stores(monkeypatch, db):
    monkeypatch.setenv("AUTH_ADMIN_REGISTER_EMAIL", "admin@dyspensr.com")
    monkeypatch.setenv("AUTH_ADMIN_REGISTER_PASSWORD", "123456")
    monkeypatch.delenv("AUTH_REGISTRATION_PASSWORD", raising=False)
    db.insert_tenant(tenant_id="t1", name="T1")
    db.insert_store_connection(
        tenant_id="t1",
        shop_domain="x.myshopify.com",
        access_token_enc="",
        scopes=["write_products"],
        status=StoreStatus.active,
        token_source="manual",
        store_id="store-aaa",
    )
    body = RegisterRequest(
        email="writer@example.com",
        password="userpass12",
        admin_email="admin@dyspensr.com",
        admin_password="123456",
        access="write",
    )
    out = register(body, db)
    assert out["ok"] is True
    uid = out["user_id"]
    actor = Actor(tenant_id="t1", user_id=uid, role=Role.member)
    assert can_write_store(db, actor, "store-aaa") is True


def test_register_admin_mode_wrong_password(monkeypatch, db):
    monkeypatch.setenv("AUTH_ADMIN_REGISTER_EMAIL", "admin@dyspensr.com")
    monkeypatch.setenv("AUTH_ADMIN_REGISTER_PASSWORD", "123456")
    db.insert_tenant(tenant_id="t1", name="T1")

    body = RegisterRequest(
        email="x@example.com",
        password="userpass12",
        admin_email="admin@dyspensr.com",
        admin_password="wrong",
        access="read",
    )
    with pytest.raises(HTTPException) as exc:
        register(body, db)
    assert exc.value.status_code == 403


def test_register_legacy_registration_password(monkeypatch, db):
    monkeypatch.setenv("AUTH_ADMIN_REGISTER_EMAIL", "")
    monkeypatch.setenv("AUTH_ADMIN_REGISTER_PASSWORD", "")
    monkeypatch.setenv("AUTH_REGISTRATION_PASSWORD", "legacy-secret")
    db.insert_tenant(tenant_id="t1", name="T1")

    body = RegisterRequest(
        email="legacy@example.com",
        password="legacypass",
        registration_password="legacy-secret",
        access="read",
    )
    out = register(body, db)
    assert out["ok"] is True
