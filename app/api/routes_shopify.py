from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import get_current_actor
from app.audit import audit
from app.authz import Actor
from app.db import get_db
from app.models import StoreStatus
from app.mongo_repository import MongoRepository
from app.settings import get_settings
from app.shopify.oauth import (
    build_oauth_install_url,
    decode_oauth_state,
    encode_oauth_state,
    exchange_code_for_token,
    verify_shopify_hmac,
)
from app.shopify.token_store import upsert_store_token


router = APIRouter(prefix="/shopify", tags=["shopify"])


@router.get("/install")
async def shopify_install(
    shop: str = Query(...),
    actor: Actor = Depends(get_current_actor),
    db: MongoRepository = Depends(get_db),
):
    """
    Starts OAuth for a specific shop in the authenticated tenant context.
    """
    tenant_id = actor.tenant_id
    install_url, nonce = build_oauth_install_url(shop=shop, tenant_id=tenant_id)
    state = encode_oauth_state(tenant_id=tenant_id, user_id=actor.user_id, state=nonce)
    db.insert_oauth_state(tenant_id=tenant_id, user_id=actor.user_id, nonce=nonce)
    install_url = install_url.replace(f"state={nonce}", f"state={state}")
    audit(
        db,
        tenant_id=tenant_id,
        user_id=actor.user_id,
        event_type="oauth_install_start",
        payload={"shop": shop},
    )
    return {"install_url": install_url}


@router.get("/callback")
async def shopify_callback(request: Request, db: MongoRepository = Depends(get_db)):
    settings = get_settings()
    qp = dict(request.query_params)

    if not verify_shopify_hmac(qp, client_secret=settings.shopify_app_client_secret):
        raise HTTPException(status_code=400, detail="Invalid Shopify HMAC")

    shop = qp.get("shop")
    code = qp.get("code")
    state = qp.get("state")
    if not shop or not code or not state:
        raise HTTPException(status_code=400, detail="Missing shop/code/state")

    tenant_id, user_id, nonce, _ts = decode_oauth_state(state)
    state_row = db.get_oauth_state(tenant_id, user_id, nonce)
    if not state_row:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    db.delete_oauth_state_by_id(state_row.id)

    token = await exchange_code_for_token(shop=shop, code=code)

    existing = db.get_store_by_tenant_domain(tenant_id, shop)
    if existing:
        db.update_store_connection(
            existing.id,
            {
                "access_token_enc": "",
                "scopes": token.scope,
                "status": StoreStatus.active.value,
                "installed_at": datetime.now(timezone.utc),
                "token_source": "oauth",
            },
        )
        store = db.get_stores_by_ids(tenant_id, [existing.id])[0]
    else:
        store = db.insert_store_connection(
            tenant_id=tenant_id,
            shop_domain=shop,
            access_token_enc="",
            scopes=token.scope,
            status=StoreStatus.active,
            token_source="oauth",
        )

    upsert_store_token(
        store_id=store.id,
        tenant_id=tenant_id,
        shop_domain=shop,
        access_token=token.access_token,
        scopes=token.scope,
    )
    audit(
        db,
        tenant_id=tenant_id,
        user_id=user_id or None,
        event_type="oauth_install_complete",
        payload={"shop": shop, "scopes": token.scope},
        store_id=store.id,
    )
    return {"ok": True, "tenant_id": tenant_id, "shop": shop, "store_id": store.id, "scopes": token.scope}
