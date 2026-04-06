from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import get_current_actor
from app.audit import audit
from app.authz import Actor, list_accessible_stores, require_store_write_access
from app.db import get_db
from app.lang.agent import run_agent
from app.lang.schemas import ChatRequest, ChatResponse, ConfirmRequest, StoreChoice
from app.models import Conversation
from app.mongo_repository import MongoRepository
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
    if s.endswith(".myshopify.") and not s.endswith(".myshopify.com"):
        s = s + "com"
    if ".myshopify.com" not in s and re.match(r"^[a-z0-9][a-z0-9-]*$", s):
        s = s + ".myshopify.com"
    return s


def _metadata_safe(tool_calls: list[Any]) -> dict[str, Any]:
    try:
        return json.loads(json.dumps({"tool_calls": tool_calls or []}, default=str))
    except Exception:  # noqa: BLE001
        return {"tool_calls": []}


def _ensure_conversation(db: MongoRepository, actor: Actor, conversation_id: Optional[str]) -> Conversation:
    cid = (conversation_id or "").strip()
    if cid:
        conv = db.get_conversation(cid)
        if conv is not None:
            if conv.tenant_id != actor.tenant_id or conv.user_id != actor.user_id:
                raise HTTPException(status_code=404, detail="Conversation not found")
            return conv
        return db.insert_conversation(
            conversation_id=cid,
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            title=None,
        )
    return db.insert_conversation(
        conversation_id=str(uuid.uuid4()),
        tenant_id=actor.tenant_id,
        user_id=actor.user_id,
        title=None,
    )


def _maybe_set_title_from_message(db: MongoRepository, conv: Conversation, message: str) -> None:
    if conv.title:
        return
    m = (message or "").strip().replace("\n", " ")
    if not m:
        return
    title = m[:120] + ("..." if len(m) > 120 else "")
    db.update_conversation(conv.id, {"title": title})


def _append_conversation_message(
    db: MongoRepository,
    *,
    conversation_id: str,
    role: str,
    content: str,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    db.insert_conversation_message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        message_metadata=metadata,
    )


def _with_conversation_id(resp: ChatResponse, conversation_id: str) -> ChatResponse:
    return resp.model_copy(update={"conversation_id": conversation_id})


def _resolve_store_ids(db: MongoRepository, actor: Actor, req: ChatRequest) -> Union[List[str], ChatResponse]:
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
    actor: Actor = Depends(get_current_actor),
    db: MongoRepository = Depends(get_db),
):
    t0 = time.perf_counter()
    body = body.model_copy(update={"shop_domain": _normalize_shop_domain(body.shop_domain)})
    msg = body.message or ""
    conv = _ensure_conversation(db, actor, body.conversation_id)
    cid = conv.id
    _log.info(
        "chat_start tenant=%s user=%s conv=%s msg_len=%d store_id=%s shop_domain=%s",
        actor.tenant_id,
        actor.user_id,
        cid,
        len(msg),
        body.store_id or "",
        body.shop_domain or "",
    )
    store_ids_or_resp = _resolve_store_ids(db, actor, body)
    if isinstance(store_ids_or_resp, ChatResponse):
        _log.info(
            "chat_end tenant=%s user=%s conv=%s phase=early_response type=%s ms=%.0f",
            actor.tenant_id,
            actor.user_id,
            cid,
            store_ids_or_resp.type,
            (time.perf_counter() - t0) * 1000,
        )
        if not body.skip_user_message_persist:
            _append_conversation_message(db, conversation_id=cid, role="user", content=body.message)
            _maybe_set_title_from_message(db, conv, body.message)
        db.update_conversation(cid, {})
        return _with_conversation_id(store_ids_or_resp, cid)

    store_ids = store_ids_or_resp

    if not body.skip_user_message_persist:
        _append_conversation_message(db, conversation_id=cid, role="user", content=body.message)
        _maybe_set_title_from_message(db, conv, body.message)
    db.update_conversation(cid, {})

    audit(
        db,
        tenant_id=actor.tenant_id,
        user_id=actor.user_id,
        event_type="chat_request",
        payload={"message": body.message, "store_ids": store_ids, "conversation_id": cid},
    )

    mcp_session = getattr(request.app.state, "mcp_session", None)
    checkpointer = getattr(request.app.state, "memory", None)

    try:
        result = run_agent(
            db,
            actor=actor,
            store_ids=store_ids,
            user_message=body.message,
            conversation_id=cid,
            mcp_session=mcp_session,
            checkpointer=checkpointer,
        )
    except Exception as e:  # noqa: BLE001
        _log.exception(
            "chat_agent_error tenant=%s user=%s conv=%s stores=%s",
            actor.tenant_id,
            actor.user_id,
            cid,
            store_ids,
        )
        raise HTTPException(status_code=500, detail=f"Agent error: {e}") from e

    assistant_meta = _metadata_safe(result.tool_calls)
    _append_conversation_message(
        db,
        conversation_id=cid,
        role="assistant",
        content=result.text or "",
        metadata=assistant_meta,
    )
    db.update_conversation(cid, {})

    if any(
        isinstance(tc, dict) and (tc.get("name") or "").startswith("propose_")
        for tc in (result.tool_calls or [])
    ):
        pending = db.find_latest_pending_for_conversation(actor.tenant_id, actor.user_id, cid)
        if pending:
            _log.info(
                "chat_end tenant=%s user=%s conv=%s phase=needs_confirmation pending_id=%s ms=%.0f tool_calls=%d",
                actor.tenant_id,
                actor.user_id,
                cid,
                pending.id,
                (time.perf_counter() - t0) * 1000,
                len(result.tool_calls or []),
            )
            return ChatResponse(
                type="needs_confirmation",
                message="I can make this change, but I need your confirmation first.",
                conversation_id=cid,
                pending_action_id=pending.id,
                pending_action_summary=pending.summary,
                metadata=assistant_meta,
            )
    _log.info(
        "chat_end tenant=%s user=%s conv=%s phase=message stores=%s ms=%.0f tool_calls=%d reply_len=%d",
        actor.tenant_id,
        actor.user_id,
        cid,
        store_ids,
        (time.perf_counter() - t0) * 1000,
        len(result.tool_calls or []),
        len(result.text or ""),
    )
    return ChatResponse(
        type="message",
        message=result.text or "",
        conversation_id=cid,
        metadata=assistant_meta,
    )


@router.post("/chat/confirm", response_model=ChatResponse)
def confirm(
    body: ConfirmRequest,
    actor: Actor = Depends(get_current_actor),
    db: MongoRepository = Depends(get_db),
):
    _log.info(
        "chat_confirm tenant=%s user=%s pending_action_id=%s approve=%s",
        actor.tenant_id,
        actor.user_id,
        body.pending_action_id,
        body.approve,
    )
    pending = db.get_pending_action(body.pending_action_id)
    if not pending or pending.tenant_id != actor.tenant_id or pending.user_id != actor.user_id:
        raise HTTPException(status_code=404, detail="Pending action not found")
    if pending.conversation_id:
        req_cid = (body.conversation_id or "").strip()
        if req_cid != pending.conversation_id:
            raise HTTPException(
                status_code=403,
                detail="Pending action does not belong to this conversation",
            )
    if pending.status != "pending":
        return ChatResponse(
            type="message",
            message=f"Pending action already {pending.status}.",
            conversation_id=pending.conversation_id,
        )

    if not body.approve:
        db.update_pending_action(pending.id, {"status": "cancelled"})
        audit(
            db,
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            event_type="pending_action_cancel",
            payload={"pending_action_id": pending.id},
        )
        if pending.conversation_id:
            _append_conversation_message(
                db,
                conversation_id=pending.conversation_id,
                role="assistant",
                content="Cancelled.",
                metadata={"pending_action_id": pending.id, "approved": False},
            )
            db.update_conversation(pending.conversation_id, {})
        return ChatResponse(type="message", message="Cancelled.", conversation_id=pending.conversation_id)

    for sid in pending.store_ids:
        require_store_write_access(db, actor, sid)

    stores = db.get_stores_by_ids(actor.tenant_id, pending.store_ids)
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

    db.update_pending_action(
        pending.id,
        {"status": "executed", "executed_at": datetime.now(timezone.utc)},
    )
    ok_count = sum(1 for r in results if r.get("ok"))
    if ok_count == len(results) and results:
        summary_text = "Executed."
    elif ok_count == 0:
        parts = [f"{r.get('store_id')}: {r.get('error', 'failed')}" for r in results if not r.get("ok")]
        summary_text = "Failed: " + " | ".join(parts) if parts else "Failed."
    else:
        summary_text = f"Partial: {ok_count}/{len(results)} store(s) succeeded. See metadata for errors."

    if pending.conversation_id:
        _append_conversation_message(
            db,
            conversation_id=pending.conversation_id,
            role="assistant",
            content=summary_text,
            metadata={"results": results, "pending_action_id": pending.id},
        )
        db.update_conversation(pending.conversation_id, {})
    _log.info(
        "chat_confirm_done tenant=%s user=%s pending_action_id=%s stores_ok=%d/%d",
        actor.tenant_id,
        actor.user_id,
        pending.id,
        ok_count,
        len(results),
    )
    return ChatResponse(
        type="message",
        message=summary_text,
        conversation_id=pending.conversation_id,
        metadata={"results": results},
    )
