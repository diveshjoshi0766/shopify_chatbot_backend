from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_actor
from app.audit import audit
from app.authz import Actor, require_roles
from app.db import get_db
from app.models import Role, StoreStatus
from app.mongo_repository import MongoRepository
from app.shopify.token_store import upsert_store_token


router = APIRouter(prefix="/admin", tags=["admin"])


class ManualTokenIn(BaseModel):
    shop_domain: str = Field(..., examples=["my-shop.myshopify.com"])
    access_token: str
    scopes: list[str] = Field(default_factory=list)


@router.post("/stores/manual-token")
def import_manual_token(
    body: ManualTokenIn,
    actor: Actor = Depends(get_current_actor),
    db: MongoRepository = Depends(get_db),
):
    require_roles(actor, (Role.owner, Role.admin), db)

    existing = db.get_store_by_tenant_domain(actor.tenant_id, body.shop_domain)
    if existing:
        db.update_store_connection(
            existing.id,
            {
                "access_token_enc": "",
                "scopes": body.scopes,
                "status": StoreStatus.active.value,
                "token_source": "manual",
            },
        )
        store = db.get_stores_by_ids(actor.tenant_id, [existing.id])[0]
    else:
        store = db.insert_store_connection(
            tenant_id=actor.tenant_id,
            shop_domain=body.shop_domain,
            access_token_enc="",
            scopes=body.scopes,
            status=StoreStatus.active,
            token_source="manual",
        )

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
    return {"ok": True, "store_id": store.id}


class GrantStoreWriteIn(BaseModel):
    """Optional legacy helper: writes user_store_access docs. Authorization uses role only (read_only vs member/admin/owner)."""

    user_id: str = Field(..., description="Target user id (from /auth/me)")
    store_ids: list[str] | None = Field(
        default=None,
        description="Store UUIDs; omit or null to grant write on all stores in this tenant",
    )


@router.post("/users/grant-store-write")
def grant_store_write(
    body: GrantStoreWriteIn,
    actor: Actor = Depends(get_current_actor),
    db: MongoRepository = Depends(get_db),
):
    require_roles(actor, (Role.owner, Role.admin), db)
    target = db.get_user(body.user_id)
    if not target or target.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=404, detail="User not found in this tenant")

    if body.store_ids is None:
        store_ids = [s.id for s in db.list_stores_for_tenant(actor.tenant_id)]
    elif len(body.store_ids) == 0:
        raise HTTPException(
            status_code=400,
            detail="store_ids cannot be empty; omit it to grant write on all stores in the tenant",
        )
    else:
        store_ids = list(body.store_ids)

    if not store_ids:
        raise HTTPException(status_code=400, detail="No stores in tenant to grant")

    granted: list[str] = []
    for sid in store_ids:
        stores = db.get_stores_by_ids(actor.tenant_id, [sid])
        if not stores:
            continue
        db.upsert_user_store_access(user_id=target.id, store_id=sid, can_write=True)
        granted.append(sid)

    audit(
        db,
        tenant_id=actor.tenant_id,
        user_id=actor.user_id,
        event_type="grant_store_write",
        payload={"target_user_id": target.id, "store_ids": granted},
    )
    return {"ok": True, "user_id": target.id, "store_ids": granted}
