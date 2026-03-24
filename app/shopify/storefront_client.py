from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.settings import get_settings


@dataclass(frozen=True)
class ShopifyStorefrontSession:
    shop_domain: str
    storefront_access_token: str


class ShopifyStorefrontClient:
    def __init__(self, session: ShopifyStorefrontSession):
        self.session = session
        self.settings = get_settings()

    @property
    def graphql_url(self) -> str:
        v = self.settings.shopify_storefront_api_version
        return f"https://{self.session.shop_domain}/api/{v}/graphql.json"

    def graphql(self, query: str, variables: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        headers = {
            "X-Shopify-Storefront-Access-Token": self.session.storefront_access_token,
            "Content-Type": "application/json",
        }
        payload = {"query": query, "variables": variables or {}}
        for attempt in range(5):
            with httpx.Client(timeout=30) as client:
                resp = client.post(self.graphql_url, headers=headers, json=payload)
            if resp.status_code == 429:
                time.sleep(1.5 * (attempt + 1))
                continue
            resp.raise_for_status()
            data = resp.json()
            if "errors" in data and data["errors"]:
                raise RuntimeError(f"Shopify Storefront errors: {data['errors']}")
            return data["data"]
        raise RuntimeError("Shopify Storefront throttled (too many retries)")

