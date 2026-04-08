"""
LangChain tools for EasyPost (read + propose paid actions for confirmation).
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from langchain_core.tools import tool

from app.audit import audit
from app.authz import Actor, can_write_store
from app.db import get_tool_repository
from app.easypost.client import EasyPostClient
from app.lang.policy import check_write_policy
from app.settings import get_settings

if TYPE_CHECKING:
    from app.mongo_repository import MongoRepository

_log = logging.getLogger(__name__)


def _easypost_configured() -> bool:
    return bool((get_settings().easypost_api_key or "").strip())


def _client() -> EasyPostClient:
    settings = get_settings()
    return EasyPostClient(
        api_key=(settings.easypost_api_key or "").strip(),
        base_url=(settings.easypost_api_base or "https://api.easypost.com/v2").strip(),
    )


def build_easypost_tools(
    _db: "MongoRepository",
    *,
    actor: Actor,
    store_ids: list[str],
    conversation_id: str | None,
) -> list[Any]:
    """Shipping tools scoped to tenant; paid operations use propose_* + /chat/confirm."""

    def _create_easypost_pending(*, action_type: str, payload: dict[str, Any], summary: str) -> dict[str, Any]:
        decision = check_write_policy(action_type, payload)
        if not decision.allowed:
            return {"ok": False, "reason": decision.reason}
        tool_db = get_tool_repository()
        try:
            for sid in store_ids:
                if not can_write_store(tool_db, actor, sid):
                    return {"ok": False, "reason": f"No write access for store {sid}"}
            pa = tool_db.insert_pending_action(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                conversation_id=conversation_id,
                store_ids=store_ids,
                action_type=action_type,
                tool_payload=payload,
                summary=summary,
            )
            audit(
                tool_db,
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                event_type="pending_action_create",
                payload={
                    "pending_action_id": pa.id,
                    "action_type": action_type,
                    "conversation_id": conversation_id,
                    "integration": "easypost",
                },
            )
            return {"ok": True, "pending_action_id": pa.id, "summary": summary}
        except Exception as e:  # noqa: BLE001
            _log.warning("easypost pending_action_create failed: %s", e, exc_info=True)
            return {"ok": False, "error": str(e)}

    def _ref() -> str:
        return f"{actor.tenant_id}:{actor.user_id}"

    @tool
    def easypost_retrieve_shipment(shipment_id: str) -> dict[str, Any]:
        """Fetch an EasyPost shipment by id (shp_...). Returns id, rates, postage_label if purchased."""
        if not _easypost_configured():
            return {"ok": False, "error": "EasyPost is not configured (EASYPOST_API_KEY)."}
        _log.info("tool easypost_retrieve_shipment id=%s", shipment_id)
        try:
            s = _client().get_shipment(shipment_id.strip())
            rates = s.get("rates") or []
            slim_rates = [
                {"id": r.get("id"), "carrier": r.get("carrier"), "service": r.get("service"), "rate": r.get("rate")}
                for r in rates[:40]
                if isinstance(r, dict)
            ]
            return {
                "ok": True,
                "id": s.get("id"),
                "reference": s.get("reference"),
                "status": s.get("status"),
                "rates": slim_rates,
                "postage_label": s.get("postage_label"),
                "tracker": s.get("tracker"),
            }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    @tool
    def easypost_create_shipment(from_address_json: str, to_address_json: str, parcel_json: str) -> dict[str, Any]:
        """
        Create an EasyPost shipment (unpaid). Pass each argument as a JSON object string:
        from_address, to_address (EasyPost address fields), parcel (length, width, height, weight).
        Tags reference with tenant:user for traceability.
        """
        if not _easypost_configured():
            return {"ok": False, "error": "EasyPost is not configured (EASYPOST_API_KEY)."}
        if not store_ids:
            return {"ok": False, "error": "No stores in scope; connect a store first."}
        try:
            fa = json.loads(from_address_json)
            ta = json.loads(to_address_json)
            par = json.loads(parcel_json)
            if not all(isinstance(x, dict) for x in (fa, ta, par)):
                return {"ok": False, "error": "from_address_json, to_address_json, parcel_json must be JSON objects"}
        except json.JSONDecodeError as e:
            return {"ok": False, "error": f"Invalid JSON: {e}"}
        _log.info("tool easypost_create_shipment tenant=%s", actor.tenant_id)
        try:
            s = _client().create_shipment(
                to_address=ta,
                from_address=fa,
                parcel=par,
                reference=_ref(),
            )
            rates = s.get("rates") or []
            slim_rates = [
                {"id": r.get("id"), "carrier": r.get("carrier"), "service": r.get("service"), "rate": r.get("rate")}
                for r in rates[:40]
                if isinstance(r, dict)
            ]
            return {
                "ok": True,
                "shipment_id": s.get("id"),
                "reference": s.get("reference"),
                "rates": slim_rates,
                "hint": "Use propose_easypost_buy_label with shipment_id and chosen rate id to purchase postage (requires user confirmation).",
            }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    @tool
    def propose_easypost_buy_label(shipment_id: str, rate_id: str) -> dict[str, Any]:
        """Propose purchasing a label (charges EasyPost). Executes only after user confirms in chat."""
        if not _easypost_configured():
            return {"ok": False, "error": "EasyPost is not configured (EASYPOST_API_KEY)."}
        if not store_ids:
            return {"ok": False, "error": "No stores in scope; connect a store first."}
        sid = shipment_id.strip()
        rid = rate_id.strip()
        summary = f"Buy EasyPost label for shipment {sid} with rate {rid}."
        _log.info("tool propose_easypost_buy_label shipment=%s", sid)
        return _create_easypost_pending(
            action_type="easypost_buy_label",
            payload={"shipment_id": sid, "rate_id": rid},
            summary=summary,
        )

    @tool
    def propose_easypost_refund_shipment(shipment_id: str) -> dict[str, Any]:
        """Propose voiding/refunding a shipment label (EasyPost refund). Executes only after user confirms."""
        if not _easypost_configured():
            return {"ok": False, "error": "EasyPost is not configured (EASYPOST_API_KEY)."}
        if not store_ids:
            return {"ok": False, "error": "No stores in scope; connect a store first."}
        sid = shipment_id.strip()
        summary = f"Refund EasyPost shipment / void label {sid}."
        _log.info("tool propose_easypost_refund_shipment shipment=%s", sid)
        return _create_easypost_pending(
            action_type="easypost_refund_shipment",
            payload={"shipment_id": sid},
            summary=summary,
        )

    return [
        easypost_retrieve_shipment,
        easypost_create_shipment,
        propose_easypost_buy_label,
        propose_easypost_refund_shipment,
    ]
