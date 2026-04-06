from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from app.api.deps import get_current_actor
from app.auth import issue_access_token
from app.passwords import hash_password, verify_password
from app.authz import Actor, can_write_store, list_accessible_stores
from app.db import get_db
from app.models import Role
from app.mongo_repository import MongoRepository
from app.settings import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    """Required if the account has a password set (new registrations)."""
    password: str | None = None


class RegisterRequest(BaseModel):
    email: str
    display_name: str | None = None
    password: str = Field(..., min_length=8, description="Login password for the new user")
    """Legacy gate when AUTH_ADMIN_REGISTER_* are unset; ignored when admin-register mode is active."""
    registration_password: str | None = None
    """Required when AUTH_ADMIN_REGISTER_EMAIL and AUTH_ADMIN_REGISTER_PASSWORD are set."""
    admin_email: str | None = None
    admin_password: str | None = None
    access: str | None = None  # "read" | "write"
    store_ids: list[str] | None = None


@router.post("/login")
def login(body: LoginRequest, db: MongoRepository = Depends(get_db)):
    settings = get_settings()
    tenant_id = settings.default_tenant_id.strip()
    if not tenant_id:
        raise HTTPException(status_code=500, detail="default_tenant_id is not configured")
    email = str(body.email).strip().lower()
    user = db.get_user_by_tenant_email(tenant_id, email)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    pw_in = (body.password or "").strip()
    if user.password_hash:
        if not pw_in:
            raise HTTPException(status_code=401, detail="Password required")
        if not verify_password(pw_in, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid email or password")
    token = issue_access_token(tenant_id=user.tenant_id, user_id=user.id)
    return {"access_token": token, "token_type": "bearer"}


def _str_eq_ct(a: str, b: str) -> bool:
    """Constant-time string compare for secrets."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


@router.post("/register")
def register(body: RegisterRequest, db: MongoRepository = Depends(get_db)):
    settings = get_settings()
    admin_reg_email = (settings.auth_admin_register_email or "").strip()
    admin_reg_pass = settings.auth_admin_register_password or ""

    if admin_reg_email and admin_reg_pass:
        if not (body.admin_email or "").strip() or not body.admin_password:
            raise HTTPException(
                status_code=400,
                detail="admin_email and admin_password are required",
            )
        if body.admin_email.strip().lower() != admin_reg_email.lower():
            raise HTTPException(status_code=403, detail="Invalid admin credentials")
        if not _str_eq_ct(body.admin_password, admin_reg_pass):
            raise HTTPException(status_code=403, detail="Invalid admin credentials")
    else:
        gate_password = (settings.auth_registration_password or "").strip()
        if not gate_password:
            raise HTTPException(status_code=503, detail="Registration is not configured")
        if body.registration_password != gate_password:
            raise HTTPException(status_code=403, detail="Invalid registration password")

    tenant_id = settings.default_tenant_id.strip()
    email = body.email.strip().lower()
    if not tenant_id or not email:
        raise HTTPException(status_code=400, detail="tenant_id and email are required")

    if not db.get_tenant(tenant_id):
        try:
            db.insert_tenant(tenant_id=tenant_id, name=tenant_id)
        except DuplicateKeyError:
            pass

    if db.get_user_by_tenant_email(tenant_id, email):
        raise HTTPException(status_code=409, detail="User already exists")

    requested_access = (body.access or "").strip().lower()
    if email == settings.auth_admin_email.strip().lower():
        role = Role.admin
        effective_access = "write"
    elif requested_access == "write":
        role = Role.member
        effective_access = "write"
    else:
        role = Role.read_only
        effective_access = "read"

    try:
        user = db.insert_user(
            tenant_id=tenant_id,
            email=email,
            display_name=(body.display_name or "").strip() or None,
            password_hash=hash_password(body.password),
            role=role,
        )
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="User already exists") from None

    return {"ok": True, "user_id": user.id, "role": user.role.value, "access": effective_access}


@router.get("/me")
def me(actor: Actor = Depends(get_current_actor), db: MongoRepository = Depends(get_db)):
    user = db.get_user(actor.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    stores = list_accessible_stores(db, actor)
    can_write_by_store = {s.id: can_write_store(db, actor, s.id) for s in stores}
    access = "write" if any(can_write_by_store.values()) else "read"
    memberships = [
        {"store_id": s.id, "can_write": can_write_store(db, actor, s.id)} for s in stores
    ]
    return {
        "user": {
            "id": user.id,
            "tenant_id": user.tenant_id,
            "email": user.email,
            "display_name": user.display_name,
            "role": user.role.value,
        },
        "stores": [{"store_id": s.id, "shop_domain": s.shop_domain} for s in stores],
        "can_write_by_store": can_write_by_store,
        "access": access,
        "capabilities": {
            "can_import_manual_token": actor.role.value in {"owner", "admin"},
            "has_any_write_access": any(can_write_by_store.values()),
        },
        "memberships": memberships,
    }
