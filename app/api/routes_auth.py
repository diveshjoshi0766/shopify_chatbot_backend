from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_actor
from app.auth import issue_access_token
from app.authz import Actor, can_write_store, list_accessible_stores
from app.db import get_db
from app.models import User, UserStoreAccess

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    tenant_id: str
    email: str


@router.post("/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.tenant_id == body.tenant_id, User.email == str(body.email).lower()))
    if not user:
        raise HTTPException(status_code=401, detail="Invalid tenant/email")
    token = issue_access_token(tenant_id=user.tenant_id, user_id=user.id)
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me")
def me(actor: Actor = Depends(get_current_actor), db: Session = Depends(get_db)):
    user = db.get(User, actor.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    stores = list_accessible_stores(db, actor)
    can_write_by_store = {s.id: can_write_store(db, actor, s.id) for s in stores}
    memberships = db.scalars(select(UserStoreAccess).where(UserStoreAccess.user_id == actor.user_id)).all()
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
        "capabilities": {
            "can_import_manual_token": actor.role.value in {"owner", "admin"},
            "has_any_write_access": any(can_write_by_store.values()),
        },
        "memberships": [
            {"store_id": m.store_id, "can_write": bool(m.can_write)}
            for m in memberships
        ],
    }
