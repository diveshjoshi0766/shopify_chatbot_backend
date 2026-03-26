from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_actor_from_headers
from app.audit import audit
from app.authz import Actor, list_accessible_stores
from app.db import get_db
from app.models import OAuthState
from app.settings import get_settings
from app.shopify.oauth import build_oauth_install_url, encode_oauth_state

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get("/shopify/status")
def shopify_integration_status(
    actor: Actor = Depends(get_actor_from_headers),
    db: Session = Depends(get_db),
):
    """List connected Shopify stores for this user (no tokens returned)."""
    stores = list_accessible_stores(db, actor)
    return {
        "tenant_id": actor.tenant_id,
        "stores": [
            {
                "store_id": s.id,
                "shop_domain": s.shop_domain,
                "scopes": s.scopes,
                "status": s.status.value,
                "token_source": s.token_source,
            }
            for s in stores
        ],
    }


@router.get("/shopify/oauth-install-url")
async def shopify_oauth_install_url(
    shop: str = Query(..., description="Shop handle or myshopify.com domain"),
    actor: Actor = Depends(get_actor_from_headers),
    db: Session = Depends(get_db),
):
    """
    Build Shopify OAuth install URL for the authenticated tenant (same flow as GET /shopify/install).
    """
    settings = get_settings()
    if not settings.shopify_app_client_id or not settings.shopify_app_redirect_uri:
        raise HTTPException(
            status_code=503,
            detail="Shopify app is not configured (SHOPIFY_APP_CLIENT_ID / SHOPIFY_APP_REDIRECT_URI).",
        )
    tenant_id = actor.tenant_id
    try:
        install_url, nonce = build_oauth_install_url(shop=shop, tenant_id=tenant_id)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    state = encode_oauth_state(tenant_id=tenant_id, state=nonce)
    db.add(OAuthState(tenant_id=tenant_id, nonce=nonce))
    db.commit()
    install_url = install_url.replace(f"state={nonce}", f"state={state}")
    audit(db, tenant_id=tenant_id, event_type="oauth_install_start", payload={"shop": shop})
    db.commit()
    return {"install_url": install_url, "tenant_id": tenant_id}
