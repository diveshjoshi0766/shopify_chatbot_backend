"""
Domain types and MongoDB document entity tags for the dyspensr_ai_bot collection.

All persistent rows live in one collection; `entity` discriminates document shape.
"""
from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class StoreStatus(str, enum.Enum):
    active = "active"
    disabled = "disabled"
    uninstalled = "uninstalled"


class Role(str, enum.Enum):
    owner = "owner"
    admin = "admin"
    member = "member"
    read_only = "read_only"


# Document discriminator values (single collection)
class Entity(str, enum.Enum):
    tenant = "tenant"
    user = "user"
    store_connection = "store_connection"
    user_store_access = "user_store_access"
    conversation = "conversation"
    conversation_message = "conversation_message"
    pending_action = "pending_action"
    audit_log = "audit_log"
    oauth_state = "oauth_state"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Tenant:
    id: str
    name: str
    created_at: datetime

    @staticmethod
    def from_doc(d: dict[str, Any]) -> Tenant:
        return Tenant(
            id=d["_id"],
            name=d["name"],
            created_at=d.get("created_at") or utcnow(),
        )


@dataclass
class User:
    id: str
    tenant_id: str
    email: str
    display_name: Optional[str]
    password_hash: Optional[str]
    role: Role
    created_at: datetime

    @staticmethod
    def from_doc(d: dict[str, Any]) -> User:
        return User(
            id=d["_id"],
            tenant_id=d["tenant_id"],
            email=d["email"],
            display_name=d.get("display_name"),
            password_hash=d.get("password_hash"),
            role=Role(d["role"]),
            created_at=d.get("created_at") or utcnow(),
        )


@dataclass
class StoreConnection:
    id: str
    tenant_id: str
    shop_domain: str
    shop_id: Optional[str]
    access_token_enc: str
    scopes: List[str]
    installed_at: datetime
    status: StoreStatus
    token_source: str

    @staticmethod
    def from_doc(d: dict[str, Any]) -> StoreConnection:
        return StoreConnection(
            id=d["_id"],
            tenant_id=d["tenant_id"],
            shop_domain=d["shop_domain"],
            shop_id=d.get("shop_id"),
            access_token_enc=d.get("access_token_enc") or "",
            scopes=list(d.get("scopes") or []),
            installed_at=d.get("installed_at") or utcnow(),
            status=StoreStatus(d.get("status") or StoreStatus.active.value),
            token_source=d.get("token_source") or "oauth",
        )


@dataclass
class UserStoreAccess:
    id: str
    user_id: str
    store_id: str
    can_write: bool

    @staticmethod
    def from_doc(d: dict[str, Any]) -> UserStoreAccess:
        return UserStoreAccess(
            id=d["_id"],
            user_id=d["user_id"],
            store_id=d["store_id"],
            can_write=bool(d.get("can_write")),
        )


@dataclass
class Conversation:
    id: str
    tenant_id: str
    user_id: str
    title: Optional[str]
    created_at: datetime
    updated_at: datetime

    @staticmethod
    def from_doc(d: dict[str, Any]) -> Conversation:
        return Conversation(
            id=d["_id"],
            tenant_id=d["tenant_id"],
            user_id=d["user_id"],
            title=d.get("title"),
            created_at=d.get("created_at") or utcnow(),
            updated_at=d.get("updated_at") or utcnow(),
        )


@dataclass
class ConversationMessage:
    id: str
    conversation_id: str
    role: str
    content: str
    message_metadata: Optional[Dict[str, Any]]
    created_at: datetime

    @staticmethod
    def from_doc(d: dict[str, Any]) -> ConversationMessage:
        return ConversationMessage(
            id=d["_id"],
            conversation_id=d["conversation_id"],
            role=d["role"],
            content=d["content"],
            message_metadata=d.get("message_metadata"),
            created_at=d.get("created_at") or utcnow(),
        )


@dataclass
class PendingAction:
    id: str
    tenant_id: str
    user_id: str
    conversation_id: Optional[str]
    store_ids: List[str]
    action_type: str
    tool_payload: Dict[str, Any]
    summary: str
    status: str
    created_at: datetime
    executed_at: Optional[datetime]

    @staticmethod
    def from_doc(d: dict[str, Any]) -> PendingAction:
        return PendingAction(
            id=d["_id"],
            tenant_id=d["tenant_id"],
            user_id=d["user_id"],
            conversation_id=d.get("conversation_id"),
            store_ids=list(d.get("store_ids") or []),
            action_type=d["action_type"],
            tool_payload=dict(d.get("tool_payload") or {}),
            summary=d.get("summary") or "",
            status=d.get("status") or "pending",
            created_at=d.get("created_at") or utcnow(),
            executed_at=d.get("executed_at"),
        )


@dataclass
class OAuthState:
    id: str
    tenant_id: str
    user_id: Optional[str]
    nonce: str
    created_at: datetime

    @staticmethod
    def from_doc(d: dict[str, Any]) -> OAuthState:
        return OAuthState(
            id=d["_id"],
            tenant_id=d["tenant_id"],
            user_id=d.get("user_id"),
            nonce=d["nonce"],
            created_at=d.get("created_at") or utcnow(),
        )


def new_id() -> str:
    return str(uuid.uuid4())
