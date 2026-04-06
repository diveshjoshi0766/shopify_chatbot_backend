from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.settings import get_settings

_log = logging.getLogger(__name__)

_GQL_OP_RE = re.compile(r"\b(query|mutation|subscription)\s+(\w+)", re.IGNORECASE)


def _graphql_op_hint(query: str) -> str:
    """Short label for logs (no secrets)."""
    if not (query or "").strip():
        return "empty"
    for raw in query.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _GQL_OP_RE.search(line)
        if m:
            return f"{m.group(1).lower()} {m.group(2)}"
        return line[:100] + ("…" if len(line) > 100 else "")
    return "unknown"


def _variable_keys_preview(variables: Optional[dict[str, Any]], *, limit: int = 12) -> str:
    if not variables:
        return "[]"
    keys = list(variables.keys())[:limit]
    more = len(variables) - len(keys)
    suffix = f" +{more} more" if more > 0 else ""
    return str(keys) + suffix


@dataclass(frozen=True)
class ShopifyAdminSession:
    shop_domain: str
    access_token: str


class ShopifyAdminClient:
    def __init__(self, session: ShopifyAdminSession):
        self.session = session
        self.settings = get_settings()

    @property
    def graphql_url(self) -> str:
        v = self.settings.shopify_admin_api_version
        return f"https://{self.session.shop_domain}/admin/api/{v}/graphql.json"

    def graphql(self, query: str, variables: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        headers = {
            "X-Shopify-Access-Token": self.session.access_token,
            "Content-Type": "application/json",
        }
        payload = {"query": query, "variables": variables or {}}
        vars_for_log = variables or {}
        _log.info(
            "shopify_admin_graphql request shop=%s url_tail=/admin/api/%s/graphql.json op_hint=%s variable_keys=%s",
            self.session.shop_domain,
            self.settings.shopify_admin_api_version,
            _graphql_op_hint(query),
            _variable_keys_preview(vars_for_log),
        )

        # Simple throttling/backoff loop.
        for attempt in range(5):
            with httpx.Client(timeout=30) as client:
                resp = client.post(self.graphql_url, headers=headers, json=payload)
            if resp.status_code == 429:
                _log.warning(
                    "shopify_admin_graphql 429 shop=%s attempt=%d",
                    self.session.shop_domain,
                    attempt + 1,
                )
                time.sleep(1.5 * (attempt + 1))
                continue
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError:
                _log.warning(
                    "shopify_admin_graphql http_error shop=%s status=%s body=%s",
                    self.session.shop_domain,
                    resp.status_code,
                    (resp.text or "")[:500],
                )
                raise
            data = resp.json()
            if "errors" in data and data["errors"]:
                _log.warning(
                    "shopify_admin_graphql user_errors shop=%s errors=%s",
                    self.session.shop_domain,
                    data["errors"],
                )
                raise RuntimeError(f"Shopify GraphQL errors: {data['errors']}")
            _log.info(
                "shopify_admin_graphql ok shop=%s status=%s",
                self.session.shop_domain,
                resp.status_code,
            )
            return data["data"]
        raise RuntimeError("Shopify API throttled (too many retries)")

