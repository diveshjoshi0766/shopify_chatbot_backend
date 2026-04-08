from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.api.deps import get_current_actor
from app.audit import audit
from app.authz import Actor, list_accessible_stores
from app.db import get_db
from app.easypost.webhook_verify import easypost_webhook_signature_valid
from app.mongo_repository import MongoRepository
from app.settings import get_settings
from app.shopify.oauth import build_oauth_install_url, encode_oauth_state

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get("/shopify/status")
def shopify_integration_status(
    actor: Actor = Depends(get_current_actor),
    db: MongoRepository = Depends(get_db),
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
    actor: Actor = Depends(get_current_actor),
    db: MongoRepository = Depends(get_db),
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
    return {"install_url": install_url, "tenant_id": tenant_id}


@router.get("/easypost/status")
def easypost_integration_status(actor: Actor = Depends(get_current_actor)):
    """EasyPost configuration flags (no secrets returned)."""
    settings = get_settings()
    return {
        "tenant_id": actor.tenant_id,
        "api_key_configured": bool((settings.easypost_api_key or "").strip()),
        "webhook_secret_configured": bool((settings.easypost_webhook_secret or "").strip()),
        "api_base": (settings.easypost_api_base or "").strip() or "https://api.easypost.com/v2",
    }


@router.post("/easypost/webhook")
async def easypost_webhook(request: Request, db: MongoRepository = Depends(get_db)):
    """
    EasyPost Event webhook. Validates X-Hmac-Signature when EASYPOST_WEBHOOK_SECRET is set.
    Idempotent storage by EasyPost event id (evt_...).
    """
    settings = get_settings()
    secret = (settings.easypost_webhook_secret or "").strip()
    if not secret:
        return Response(status_code=503, content="EasyPost webhook secret not configured")

    body = await request.body()
    sig = request.headers.get("X-Hmac-Signature") or request.headers.get("x-hmac-signature")
    if not easypost_webhook_signature_valid(secret=secret, raw_body=body, signature_header=sig):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="Invalid JSON body") from None

    event_id = str(payload.get("id") or "").strip()
    if not event_id:
        return {"ok": True, "stored": False, "note": "missing event id"}

    desc = str(payload.get("description") or "")
    robj = payload.get("result")
    robject = None
    if isinstance(robj, dict):
        robject = str(robj.get("object") or "") or None

    inserted = db.insert_easypost_webhook_event_if_new(
        event_id=event_id,
        description=desc,
        result_object=robject,
    )
    return {"ok": True, "stored": inserted, "event_id": event_id}
