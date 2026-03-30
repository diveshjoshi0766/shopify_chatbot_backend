from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class StoreStatus(str, enum.Enum):
    active = "active"
    disabled = "disabled"
    uninstalled = "uninstalled"


class Role(str, enum.Enum):
    owner = "owner"
    admin = "admin"
    member = "member"
    read_only = "read_only"


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    users: Mapped[List["User"]] = relationship(back_populates="tenant", cascade="all,delete-orphan")
    stores: Mapped[List["StoreConnection"]] = relationship(back_populates="tenant", cascade="all,delete-orphan")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_user_tenant_email"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    role: Mapped[Role] = mapped_column(
        Enum(Role, native_enum=False, length=32),
        nullable=False,
        default=Role.member,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    tenant: Mapped["Tenant"] = relationship(back_populates="users")
    store_memberships: Mapped[List["UserStoreAccess"]] = relationship(
        back_populates="user", cascade="all,delete-orphan"
    )


class StoreConnection(Base):
    __tablename__ = "store_connections"
    __table_args__ = (UniqueConstraint("tenant_id", "shop_domain", name="uq_store_tenant_domain"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    shop_domain: Mapped[str] = mapped_column(String(255), nullable=False)  # example: my-shop.myshopify.com
    shop_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    access_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[List[str]] = mapped_column(JSON, nullable=False, default=list)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    status: Mapped[StoreStatus] = mapped_column(
        Enum(StoreStatus, native_enum=False, length=32),
        nullable=False,
        default=StoreStatus.active,
    )
    token_source: Mapped[str] = mapped_column(String(20), nullable=False, default="oauth")  # oauth|manual

    tenant: Mapped["Tenant"] = relationship(back_populates="stores")
    user_access: Mapped[List["UserStoreAccess"]] = relationship(
        back_populates="store", cascade="all,delete-orphan"
    )


class UserStoreAccess(Base):
    __tablename__ = "user_store_access"
    __table_args__ = (UniqueConstraint("user_id", "store_id", name="uq_user_store"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    store_id: Mapped[str] = mapped_column(ForeignKey("store_connections.id", ondelete="CASCADE"), nullable=False)
    can_write: Mapped[bool] = mapped_column(nullable=False, default=False)

    user: Mapped["User"] = relationship(back_populates="store_memberships")
    store: Mapped["StoreConnection"] = relationship(back_populates="user_access")


class PendingAction(Base):
    __tablename__ = "pending_actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    store_ids: Mapped[List[str]] = mapped_column(JSON, nullable=False, default=list)
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)  # eg "update_product_price"
    tool_payload: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")  # pending|executed|cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    store_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    event_type: Mapped[str] = mapped_column(String(50), nullable=False)  # tool_call|oauth_install|authz_deny|...
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class OAuthState(Base):
    __tablename__ = "oauth_states"
    __table_args__ = (UniqueConstraint("tenant_id", "nonce", name="uq_oauth_tenant_nonce"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

