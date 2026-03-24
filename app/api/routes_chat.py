from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_actor_from_headers
from app.audit import audit
from app.authz import Actor, can_write_store, list_accessible_stores
from app.db import get_db
from app.lang.agent import run_agent
from app.lang.schemas import ChatRequest, ChatResponse, ConfirmRequest, StoreChoice
from app.models import PendingAction, StoreConnection
from app.shopify.admin_client import ShopifyAdminClient, ShopifyAdminSession
from app.shopify.executor import execute_pending_action
from app.shopify.token_store import get_access_token_for_store


router = APIRouter(tags=["chat"])
_log = logging.getLogger(__name__)

_STORE_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)


def _normalize_shop_domain(value: Optional[str]) -> Optional[str]:
    if not value or not str(value).strip():
        return None
    s = str(value).strip().lower()
    # Common typo: ...myshopify.  (missing "com")
    if s.endswith(".myshopify.") and not s.endswith(".myshopify.com"):
        s = s + "com"
    if ".myshopify.com" not in s and re.match(r"^[a-z0-9][a-z0-9-]*$", s):
        s = s + ".myshopify.com"
    return s


def _metadata_safe(tool_calls: list[Any]) -> dict[str, Any]:
    """Ensure response JSON encodes (LangChain tool payloads may contain non-JSON types)."""
    try:
        return json.loads(json.dumps({"tool_calls": tool_calls or []}, default=str))
    except Exception:  # noqa: BLE001
        return {"tool_calls": []}


def _resolve_store_ids(db: Session, actor: Actor, req: ChatRequest) -> Union[List[str], ChatResponse]:
    stores = list_accessible_stores(db, actor)
    if not stores:
        return ChatResponse(type="message", message="No Shopify stores are connected for your tenant.")

    if req.store_ids:
        accessible_ids = {s.id for s in stores}
        validated = [sid for sid in req.store_ids if sid in accessible_ids]
        if not validated:
            return ChatResponse(type="message", message="None of the selected stores are accessible.")
        denied = [sid for sid in req.store_ids if sid not in accessible_ids]
        if denied:
            _log.warning("store_ids denied for actor %s: %s", actor.user_id, denied)
        return validated

    if req.store_id:
        raw = str(req.store_id).strip()
        if _STORE_UUID_RE.match(raw):
            if any(s.id == raw for s in stores):
                return [raw]
            return ChatResponse(
                type="message",
                message="You do not have access to that store_id.",
            )
        hint = _normalize_shop_domain(raw)
        if hint:
            match = next(
                (s for s in stores if s.shop_domain.strip().lower() == hint),
                None,
            )
            if match:
                return [match.id]
        return ChatResponse(
            type="message",
            message="Unknown store. Use the UUID from Integrations, or shop domain like my-store.myshopify.com (Store field can be the handle or UUID).",
        )

    if req.shop_domain:
        want = req.shop_domain.strip().lower()
        match = next((s for s in stores if s.shop_domain.strip().lower() == want), None)
        if match:
            return [match.id]
        return ChatResponse(type="message", message="Unknown shop_domain for your tenant.")

    if len(stores) == 1:
        return [stores[0].id]

    return ChatResponse(
        type="needs_store_selection",
        message="Which Shopify store should I use?",
        stores=[StoreChoice(store_id=s.id, shop_domain=s.shop_domain) for s in stores],
    )


@router.post("/chat", response_model=ChatResponse)
def chat(
    request: Request,
    body: ChatRequest,
    actor: Actor = Depends(get_actor_from_headers),
    db: Session = Depends(get_db),
):
    t0 = time.perf_counter()
    body = body.model_copy(update={"shop_domain": _normalize_shop_domain(body.shop_domain)})
    msg = body.message or ""
    _log.info(
        "chat_start tenant=%s user=%s msg_len=%d store_id=%s shop_domain=%s",
        actor.tenant_id,
        actor.user_id,
        len(msg),
        body.store_id or "",
        body.shop_domain or "",
    )
    store_ids_or_resp = _resolve_store_ids(db, actor, body)
    if isinstance(store_ids_or_resp, ChatResponse):
        _log.info(
            "chat_end tenant=%s user=%s phase=early_response type=%s ms=%.0f",
            actor.tenant_id,
            actor.user_id,
            store_ids_or_resp.type,
            (time.perf_counter() - t0) * 1000,
        )
        return store_ids_or_resp
    store_ids = store_ids_or_resp

    audit(
        db,
        tenant_id=actor.tenant_id,
        user_id=actor.user_id,
        event_type="chat_request",
        payload={"message": body.message, "store_ids": store_ids},
    )

    mcp_session = getattr(request.app.state, "mcp_session", None)
    checkpointer = getattr(request.app.state, "memory", None)

    try:
        result = run_agent(
            db,
            actor=actor,
            store_ids=store_ids,
            user_message=body.message,
            mcp_session=mcp_session,
            checkpointer=checkpointer,
        )
    except Exception as e:  # noqa: BLE001
        _log.exception(
            "chat_agent_error tenant=%s user=%s stores=%s",
            actor.tenant_id,
            actor.user_id,
            store_ids,
        )
        raise HTTPException(status_code=500, detail=f"Agent error: {e}") from e

    if any(
        isinstance(tc, dict) and (tc.get("name") or "").startswith("propose_")
        for tc in (result.tool_calls or [])
    ):
        pending = db.scalar(
            select(PendingAction)
            .where(
                PendingAction.tenant_id == actor.tenant_id,
                PendingAction.user_id == actor.user_id,
                PendingAction.status == "pending",
            )
            .order_by(PendingAction.created_at.desc())
        )
        if pending:
            _log.info(
                "chat_end tenant=%s user=%s phase=needs_confirmation pending_id=%s ms=%.0f tool_calls=%d",
                actor.tenant_id,
                actor.user_id,
                pending.id,
                (time.perf_counter() - t0) * 1000,
                len(result.tool_calls or []),
            )
            return ChatResponse(
                type="needs_confirmation",
                message="I can make this change, but I need your confirmation first.",
                pending_action_id=pending.id,
                pending_action_summary=pending.summary,
                metadata=_metadata_safe(result.tool_calls),
            )
    _log.info(
        "chat_end tenant=%s user=%s phase=message stores=%s ms=%.0f tool_calls=%d reply_len=%d",
        actor.tenant_id,
        actor.user_id,
        store_ids,
        (time.perf_counter() - t0) * 1000,
        len(result.tool_calls or []),
        len(result.text or ""),
    )
    return ChatResponse(
        type="message",
        message=result.text or "",
        metadata=_metadata_safe(result.tool_calls),
    )


@router.post("/chat/confirm", response_model=ChatResponse)
def confirm(
    body: ConfirmRequest,
    actor: Actor = Depends(get_actor_from_headers),
    db: Session = Depends(get_db),
):
    _log.info(
        "chat_confirm tenant=%s user=%s pending_action_id=%s approve=%s",
        actor.tenant_id,
        actor.user_id,
        body.pending_action_id,
        body.approve,
    )
    pending = db.get(PendingAction, body.pending_action_id)
    if not pending or pending.tenant_id != actor.tenant_id or pending.user_id != actor.user_id:
        raise HTTPException(status_code=404, detail="Pending action not found")
    if pending.status != "pending":
        return ChatResponse(type="message", message=f"Pending action already {pending.status}.")

    if not body.approve:
        pending.status = "cancelled"
        db.commit()
        audit(
            db,
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            event_type="pending_action_cancel",
            payload={"pending_action_id": pending.id},
        )
        return ChatResponse(type="message", message="Cancelled.")

    # Enforce write authorization on all targeted stores.
    for sid in pending.store_ids:
        if not can_write_store(db, actor, sid):
            raise HTTPException(status_code=403, detail=f"No write access for store {sid}")

    stores = list(
        db.scalars(
            select(StoreConnection).where(
                StoreConnection.tenant_id == actor.tenant_id, StoreConnection.id.in_(pending.store_ids)
            )
        ).all()
    )
    store_by_id = {s.id: s for s in stores}

    results: list[dict] = []
    for sid in pending.store_ids:
        store = store_by_id.get(sid)
        if not store:
            results.append({"store_id": sid, "ok": False, "error": "Store not found"})
            continue
        token = get_access_token_for_store(store)
        client = ShopifyAdminClient(ShopifyAdminSession(shop_domain=store.shop_domain, access_token=token))
        try:
            details = execute_pending_action(client=client, action_type=pending.action_type, payload=pending.tool_payload)
            results.append({"store_id": sid, "ok": True, "details": details})
            audit(
                db,
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                store_id=sid,
                event_type="pending_action_execute",
                payload={"pending_action_id": pending.id, "action_type": pending.action_type, "details": details},
            )
        except Exception as e:  # noqa: BLE001
            results.append({"store_id": sid, "ok": False, "error": str(e)})
            audit(
                db,
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                store_id=sid,
                event_type="pending_action_execute_error",
                payload={"pending_action_id": pending.id, "action_type": pending.action_type, "error": str(e)},
            )

    pending.status = "executed"
    pending.executed_at = datetime.now(timezone.utc)
    db.commit()
    ok_count = sum(1 for r in results if r.get("ok"))
    _log.info(
        "chat_confirm_done tenant=%s user=%s pending_action_id=%s stores_ok=%d/%d",
        actor.tenant_id,
        actor.user_id,
        pending.id,
        ok_count,
        len(results),
    )
    return ChatResponse(type="message", message="Executed.", metadata={"results": results})

