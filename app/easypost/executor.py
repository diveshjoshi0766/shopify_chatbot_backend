"""
Execute confirmed pending EasyPost actions (buy label, refund / void label).
"""
from __future__ import annotations

from typing import Any

from app.easypost.client import EasyPostClient
from app.settings import get_settings


def easypost_client_from_settings() -> EasyPostClient:
    settings = get_settings()
    key = (settings.easypost_api_key or "").strip()
    if not key:
        raise RuntimeError("EasyPost is not configured (EASYPOST_API_KEY).")
    base = (settings.easypost_api_base or "https://api.easypost.com/v2").strip()
    return EasyPostClient(api_key=key, base_url=base)


def execute_easypost_pending_action(*, action_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    client = easypost_client_from_settings()
    if action_type == "easypost_buy_label":
        sid = str(payload.get("shipment_id") or "").strip()
        rate_id = str(payload.get("rate_id") or "").strip()
        if not sid or not rate_id:
            raise ValueError("shipment_id and rate_id are required")
        return client.buy_shipment(sid, rate_id)

    if action_type == "easypost_refund_shipment":
        sid = str(payload.get("shipment_id") or "").strip()
        if not sid:
            raise ValueError("shipment_id is required")
        return client.refund_shipment(sid)

    raise ValueError(f"Unknown EasyPost action_type: {action_type}")
