from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_actor_from_headers
from app.audit import audit
from app.authz import Actor
from app.db import get_db
from app.models import Role, StoreConnection, StoreStatus
from app.shopify.token_store import upsert_store_token


router = APIRouter(prefix="/admin", tags=["admin"])


class ManualTokenIn(BaseModel):
    shop_domain: str = Field(..., examples=["my-shop.myshopify.com"])
    access_token: str
    scopes: list[str] = Field(default_factory=list)


@router.post("/stores/manual-token")
def import_manual_token(
    body: ManualTokenIn,
    actor: Actor = Depends(get_actor_from_headers),
    db: Session = Depends(get_db),
):
    if actor.role not in (Role.owner, Role.admin):
        raise HTTPException(status_code=403, detail="Only owner/admin can import tokens")

    existing = db.scalar(
        select(StoreConnection).where(
            StoreConnection.tenant_id == actor.tenant_id, StoreConnection.shop_domain == body.shop_domain
        )
    )
    if existing:
        existing.access_token_enc = ""
        existing.scopes = body.scopes
        existing.status = StoreStatus.active
        existing.token_source = "manual"
        store = existing
    else:
        store = StoreConnection(
            tenant_id=actor.tenant_id,
            shop_domain=body.shop_domain,
            access_token_enc="",
            scopes=body.scopes,
            status=StoreStatus.active,
            token_source="manual",
        )
        db.add(store)

    db.commit()
    upsert_store_token(
        store_id=store.id,
        tenant_id=actor.tenant_id,
        shop_domain=body.shop_domain,
        access_token=body.access_token,
        scopes=body.scopes,
    )
    audit(
        db,
        tenant_id=actor.tenant_id,
        user_id=actor.user_id,
        store_id=store.id,
        event_type="manual_token_import",
        payload={"shop": body.shop_domain, "scopes": body.scopes},
    )
    db.commit()
    return {"ok": True, "store_id": store.id}

