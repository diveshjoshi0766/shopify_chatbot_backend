from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.db import get_db
from app.models import OAuthState, StoreConnection, StoreStatus
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
    tenant_id: str = Query(...),
    shop: str = Query(...),
    db: Session = Depends(get_db),
):
    """
    Starts OAuth for a specific shop. The caller must provide a tenant_id (your system).
    """
    install_url, nonce = build_oauth_install_url(shop=shop, tenant_id=tenant_id)
    state = encode_oauth_state(tenant_id=tenant_id, state=nonce)
    db.add(OAuthState(tenant_id=tenant_id, nonce=nonce))
    db.commit()
    # Replace state in URL (build_oauth_install_url returns raw nonce; we store encoded state)
    install_url = install_url.replace(f"state={nonce}", f"state={state}")
    audit(db, tenant_id=tenant_id, event_type="oauth_install_start", payload={"shop": shop})
    return {"install_url": install_url}


@router.get("/callback")
async def shopify_callback(request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    qp = dict(request.query_params)

    if not verify_shopify_hmac(qp, client_secret=settings.shopify_app_client_secret):
        raise HTTPException(status_code=400, detail="Invalid Shopify HMAC")

    shop = qp.get("shop")
    code = qp.get("code")
    state = qp.get("state")
    if not shop or not code or not state:
        raise HTTPException(status_code=400, detail="Missing shop/code/state")

    tenant_id, nonce, _ts = decode_oauth_state(state)
    state_row = db.scalar(select(OAuthState).where(OAuthState.tenant_id == tenant_id, OAuthState.nonce == nonce))
    if not state_row:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    db.delete(state_row)
    db.commit()

    token = await exchange_code_for_token(shop=shop, code=code)

    existing = db.scalar(
        select(StoreConnection).where(StoreConnection.tenant_id == tenant_id, StoreConnection.shop_domain == shop)
    )
    if existing:
        existing.access_token_enc = ""
        existing.scopes = token.scope
        existing.status = StoreStatus.active
        existing.installed_at = datetime.now(timezone.utc)
        existing.token_source = "oauth"
        store = existing
    else:
        store = StoreConnection(
            tenant_id=tenant_id,
            shop_domain=shop,
            access_token_enc="",
            scopes=token.scope,
            status=StoreStatus.active,
            token_source="oauth",
        )
        db.add(store)

    db.commit()
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
        event_type="oauth_install_complete",
        payload={"shop": shop, "scopes": token.scope},
        store_id=store.id,
    )
    return {"ok": True, "tenant_id": tenant_id, "shop": shop, "store_id": store.id, "scopes": token.scope}

