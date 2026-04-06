"""
MongoDB access for the single collection `dyspensr_ai_bot` (name from settings).

Thread-safe for concurrent FastAPI requests and LangGraph tool threads when using
one shared PyMongo client (see app.db).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from pymongo import ASCENDING, DESCENDING
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.models import (
    Conversation,
    Entity,
    OAuthState,
    PendingAction,
    Role,
    StoreConnection,
    StoreStatus,
    Tenant,
    User,
    UserStoreAccess,
    new_id,
    utcnow,
)

_log = logging.getLogger(__name__)


def ensure_mongo_indexes(coll: Collection) -> None:
    """Idempotent index creation for the unified collection."""
    try:
        coll.create_index(
            [("entity", ASCENDING), ("tenant_id", ASCENDING), ("email", ASCENDING)],
            unique=True,
            partialFilterExpression={"entity": Entity.user.value},
            name="uq_user_tenant_email",
        )
        coll.create_index(
            [("entity", ASCENDING), ("name", ASCENDING)],
            unique=True,
            partialFilterExpression={"entity": Entity.tenant.value},
            name="uq_tenant_name",
        )
        coll.create_index(
            [("entity", ASCENDING), ("tenant_id", ASCENDING), ("shop_domain", ASCENDING)],
            unique=True,
            partialFilterExpression={"entity": Entity.store_connection.value},
            name="uq_store_tenant_domain",
        )
        coll.create_index(
            [("entity", ASCENDING), ("user_id", ASCENDING), ("store_id", ASCENDING)],
            unique=True,
            partialFilterExpression={"entity": Entity.user_store_access.value},
            name="uq_user_store",
        )
        coll.create_index(
            [("entity", ASCENDING), ("tenant_id", ASCENDING), ("nonce", ASCENDING)],
            unique=True,
            partialFilterExpression={"entity": Entity.oauth_state.value},
            name="uq_oauth_tenant_nonce",
        )
        coll.create_index(
            [("entity", ASCENDING), ("conversation_id", ASCENDING), ("created_at", ASCENDING)],
            name="idx_msg_conv_time",
        )
        coll.create_index(
            [
                ("entity", ASCENDING),
                ("tenant_id", ASCENDING),
                ("user_id", ASCENDING),
                ("conversation_id", ASCENDING),
                ("status", ASCENDING),
                ("created_at", DESCENDING),
            ],
            name="idx_pending_lookup",
        )
        _log.info("MongoDB indexes ensured for collection %s", coll.name)
    except PyMongoError as e:
        _log.warning("MongoDB index ensure failed (may already exist): %s", e)


class MongoRepository:
    def __init__(self, coll: Collection):
        self._c = coll

    def ping(self) -> None:
        self._c.database.command("ping")

    # --- Tenant ---
    def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        d = self._c.find_one({"_id": tenant_id, "entity": Entity.tenant.value})
        return Tenant.from_doc(d) if d else None

    def insert_tenant(self, *, tenant_id: str, name: str) -> Tenant:
        now = utcnow()
        doc = {"_id": tenant_id, "entity": Entity.tenant.value, "name": name, "created_at": now}
        self._c.insert_one(doc)
        return Tenant.from_doc(doc)

    # --- User ---
    def get_user(self, user_id: str) -> Optional[User]:
        d = self._c.find_one({"_id": user_id, "entity": Entity.user.value})
        return User.from_doc(d) if d else None

    def get_user_by_tenant_email(self, tenant_id: str, email: str) -> Optional[User]:
        d = self._c.find_one(
            {
                "entity": Entity.user.value,
                "tenant_id": tenant_id,
                "email": email.lower(),
            }
        )
        return User.from_doc(d) if d else None

    def insert_user(
        self,
        *,
        tenant_id: str,
        email: str,
        display_name: Optional[str],
        password_hash: Optional[str],
        role: Role,
        user_id: Optional[str] = None,
    ) -> User:
        uid = user_id or new_id()
        now = utcnow()
        doc = {
            "_id": uid,
            "entity": Entity.user.value,
            "tenant_id": tenant_id,
            "email": email.lower(),
            "display_name": display_name,
            "password_hash": password_hash,
            "role": role.value,
            "created_at": now,
        }
        self._c.insert_one(doc)
        return User.from_doc(doc)

    def update_user(self, user_id: str, updates: dict[str, Any]) -> None:
        self._c.update_one(
            {"_id": user_id, "entity": Entity.user.value},
            {"$set": updates},
        )

    def insert_user_store_access(self, *, user_id: str, store_id: str, can_write: bool) -> None:
        doc = {
            "_id": new_id(),
            "entity": Entity.user_store_access.value,
            "user_id": user_id,
            "store_id": store_id,
            "can_write": can_write,
        }
        self._c.insert_one(doc)

    def upsert_user_store_access(self, *, user_id: str, store_id: str, can_write: bool) -> None:
        existing = self.get_user_store_access(user_id, store_id)
        if existing:
            self._c.update_one(
                {"_id": existing.id, "entity": Entity.user_store_access.value},
                {"$set": {"can_write": can_write}},
            )
        else:
            self.insert_user_store_access(user_id=user_id, store_id=store_id, can_write=can_write)

    def list_user_store_access(self, user_id: str) -> list[UserStoreAccess]:
        cur = self._c.find({"entity": Entity.user_store_access.value, "user_id": user_id})
        return [UserStoreAccess.from_doc(d) for d in cur]

    # --- Store ---
    def list_stores_for_tenant(self, tenant_id: str) -> list[StoreConnection]:
        cur = self._c.find({"entity": Entity.store_connection.value, "tenant_id": tenant_id})
        return [StoreConnection.from_doc(d) for d in cur]

    def get_stores_by_ids(self, tenant_id: str, store_ids: list[str]) -> list[StoreConnection]:
        if not store_ids:
            return []
        cur = self._c.find(
            {
                "entity": Entity.store_connection.value,
                "tenant_id": tenant_id,
                "_id": {"$in": store_ids},
            }
        )
        return [StoreConnection.from_doc(d) for d in cur]

    def get_store_by_tenant_domain(self, tenant_id: str, shop_domain: str) -> Optional[StoreConnection]:
        d = self._c.find_one(
            {
                "entity": Entity.store_connection.value,
                "tenant_id": tenant_id,
                "shop_domain": shop_domain,
            }
        )
        return StoreConnection.from_doc(d) if d else None

    def insert_store_connection(
        self,
        *,
        tenant_id: str,
        shop_domain: str,
        access_token_enc: str,
        scopes: list[str],
        status: StoreStatus,
        token_source: str,
        store_id: Optional[str] = None,
    ) -> StoreConnection:
        sid = store_id or new_id()
        now = utcnow()
        doc = {
            "_id": sid,
            "entity": Entity.store_connection.value,
            "tenant_id": tenant_id,
            "shop_domain": shop_domain,
            "shop_id": None,
            "access_token_enc": access_token_enc,
            "scopes": scopes,
            "installed_at": now,
            "status": status.value,
            "token_source": token_source,
        }
        self._c.insert_one(doc)
        return StoreConnection.from_doc(doc)

    def update_store_connection(self, store_id: str, updates: dict[str, Any]) -> None:
        self._c.update_one(
            {"_id": store_id, "entity": Entity.store_connection.value},
            {"$set": updates},
        )

    # --- UserStoreAccess lookup ---
    def get_user_store_access(self, user_id: str, store_id: str) -> Optional[UserStoreAccess]:
        d = self._c.find_one(
            {
                "entity": Entity.user_store_access.value,
                "user_id": user_id,
                "store_id": store_id,
            }
        )
        return UserStoreAccess.from_doc(d) if d else None

    # --- Conversation ---
    def get_conversation(self, conversation_id: str) -> Optional[Conversation]:
        d = self._c.find_one({"_id": conversation_id, "entity": Entity.conversation.value})
        return Conversation.from_doc(d) if d else None

    def insert_conversation(
        self,
        *,
        conversation_id: str,
        tenant_id: str,
        user_id: str,
        title: Optional[str] = None,
    ) -> Conversation:
        now = utcnow()
        doc = {
            "_id": conversation_id,
            "entity": Entity.conversation.value,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
        }
        self._c.insert_one(doc)
        return Conversation.from_doc(doc)

    def update_conversation(self, conversation_id: str, updates: dict[str, Any]) -> None:
        updates = {**updates, "updated_at": utcnow()}
        self._c.update_one(
            {"_id": conversation_id, "entity": Entity.conversation.value},
            {"$set": updates},
        )

    def insert_conversation_message(
        self,
        *,
        conversation_id: str,
        role: str,
        content: str,
        message_metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        doc = {
            "_id": new_id(),
            "entity": Entity.conversation_message.value,
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "message_metadata": message_metadata,
            "created_at": utcnow(),
        }
        self._c.insert_one(doc)

    # --- Pending action ---
    def get_pending_action(self, pending_id: str) -> Optional[PendingAction]:
        d = self._c.find_one({"_id": pending_id, "entity": Entity.pending_action.value})
        return PendingAction.from_doc(d) if d else None

    def find_latest_pending_for_conversation(
        self, tenant_id: str, user_id: str, conversation_id: str
    ) -> Optional[PendingAction]:
        d = self._c.find_one(
            {
                "entity": Entity.pending_action.value,
                "tenant_id": tenant_id,
                "user_id": user_id,
                "conversation_id": conversation_id,
                "status": "pending",
            },
            sort=[("created_at", DESCENDING)],
        )
        return PendingAction.from_doc(d) if d else None

    def insert_pending_action(
        self,
        *,
        tenant_id: str,
        user_id: str,
        conversation_id: Optional[str],
        store_ids: list[str],
        action_type: str,
        tool_payload: dict[str, Any],
        summary: str,
        pending_id: Optional[str] = None,
    ) -> PendingAction:
        pid = pending_id or new_id()
        now = utcnow()
        doc = {
            "_id": pid,
            "entity": Entity.pending_action.value,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "conversation_id": conversation_id,
            "store_ids": store_ids,
            "action_type": action_type,
            "tool_payload": tool_payload,
            "summary": summary,
            "status": "pending",
            "created_at": now,
            "executed_at": None,
        }
        self._c.insert_one(doc)
        return PendingAction.from_doc(doc)

    def update_pending_action(self, pending_id: str, updates: dict[str, Any]) -> None:
        self._c.update_one(
            {"_id": pending_id, "entity": Entity.pending_action.value},
            {"$set": updates},
        )

    # --- OAuth state ---
    def insert_oauth_state(self, *, tenant_id: str, user_id: Optional[str], nonce: str) -> OAuthState:
        now = utcnow()
        doc = {
            "_id": new_id(),
            "entity": Entity.oauth_state.value,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "nonce": nonce,
            "created_at": now,
        }
        self._c.insert_one(doc)
        return OAuthState.from_doc(doc)

    def get_oauth_state(self, tenant_id: str, user_id: Optional[str], nonce: str) -> Optional[OAuthState]:
        q: dict[str, Any] = {
            "entity": Entity.oauth_state.value,
            "tenant_id": tenant_id,
            "nonce": nonce,
        }
        if user_id is not None:
            q["user_id"] = user_id
        d = self._c.find_one(q)
        return OAuthState.from_doc(d) if d else None

    def delete_oauth_state_by_id(self, state_id: str) -> None:
        self._c.delete_one({"_id": state_id, "entity": Entity.oauth_state.value})

    # --- Audit ---
    def insert_audit(
        self,
        *,
        tenant_id: str,
        event_type: str,
        payload: dict[str, Any],
        user_id: Optional[str] = None,
        store_id: Optional[str] = None,
    ) -> None:
        doc = {
            "_id": new_id(),
            "entity": Entity.audit_log.value,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "store_id": store_id,
            "event_type": event_type,
            "payload": payload,
            "created_at": utcnow(),
        }
        self._c.insert_one(doc)


__all__ = ["MongoRepository", "ensure_mongo_indexes", "DuplicateKeyError"]
