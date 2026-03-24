from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
import urllib.parse
from dataclasses import dataclass

import httpx

from app.settings import get_settings
from app.shopify.scopes import parse_scopes


def _normalize_shop_domain(shop: str) -> str:
    shop = shop.strip().lower()
    shop = shop.removeprefix("https://").removeprefix("http://")
    shop = shop.split("/")[0]
    return shop


def build_oauth_install_url(*, shop: str, tenant_id: str) -> tuple[str, str]:
    settings = get_settings()
    if not settings.shopify_app_client_id or not settings.shopify_app_redirect_uri:
        raise RuntimeError("Shopify app config is missing (SHOPIFY_APP_CLIENT_ID / SHOPIFY_APP_REDIRECT_URI)")
    shop_domain = _normalize_shop_domain(shop)
    state = secrets.token_urlsafe(32)

    params = {
        "client_id": settings.shopify_app_client_id,
        "scope": ",".join(parse_scopes(settings.shopify_app_scopes)),
        "redirect_uri": settings.shopify_app_redirect_uri,
        "state": state,
    }
    url = f"https://{shop_domain}/admin/oauth/authorize?{urllib.parse.urlencode(params)}"
    return url, state


def verify_shopify_hmac(query_params: dict[str, str], *, client_secret: str) -> bool:
    """
    Shopify sends HMAC as hex digest of the sorted query parameters (excluding hmac, signature).
    """
    params = {k: v for k, v in query_params.items() if k not in ("hmac", "signature")}
    message = urllib.parse.urlencode(sorted(params.items()))
    digest = hmac.new(client_secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    received = query_params.get("hmac", "")
    return hmac.compare_digest(digest, received)


@dataclass(frozen=True)
class TokenExchangeResult:
    access_token: str
    scope: list[str]


async def exchange_code_for_token(*, shop: str, code: str) -> TokenExchangeResult:
    settings = get_settings()
    if not settings.shopify_app_client_id or not settings.shopify_app_client_secret:
        raise RuntimeError("Shopify app config is missing (SHOPIFY_APP_CLIENT_ID / SHOPIFY_APP_CLIENT_SECRET)")
    shop_domain = _normalize_shop_domain(shop)
    url = f"https://{shop_domain}/admin/oauth/access_token"
    payload = {
        "client_id": settings.shopify_app_client_id,
        "client_secret": settings.shopify_app_client_secret,
        "code": code,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
    scopes = parse_scopes(data.get("scope", ""))
    return TokenExchangeResult(access_token=data["access_token"], scope=scopes)


def encode_oauth_state(*, tenant_id: str, state: str) -> str:
    # Keep it simple: base64url JSON-ish "tenant|state|ts"
    ts = str(int(time.time()))
    raw = f"{tenant_id}|{state}|{ts}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def decode_oauth_state(state_b64: str) -> tuple[str, str, int]:
    padded = state_b64 + "=" * ((4 - (len(state_b64) % 4)) % 4)
    raw = base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8")
    tenant_id, state, ts = raw.split("|")
    return tenant_id, state, int(ts)

