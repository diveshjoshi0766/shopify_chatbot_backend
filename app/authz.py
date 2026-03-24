from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Role, StoreConnection, User, UserStoreAccess


@dataclass(frozen=True)
class Actor:
    tenant_id: str
    user_id: str
    role: Role


def get_actor(db: Session, tenant_id: str, user_id: str) -> Actor:
    user = db.get(User, user_id)
    if not user or user.tenant_id != tenant_id:
        raise PermissionError("Unknown user")
    return Actor(tenant_id=tenant_id, user_id=user.id, role=user.role)


def list_accessible_stores(db: Session, actor: Actor) -> list[StoreConnection]:
    # Owners/admins default to all stores for the tenant.
    if actor.role in (Role.owner, Role.admin):
        return list(
            db.scalars(select(StoreConnection).where(StoreConnection.tenant_id == actor.tenant_id)).all()
        )
    # Members default to explicit mappings.
    store_ids = list(
        db.scalars(select(UserStoreAccess.store_id).where(UserStoreAccess.user_id == actor.user_id)).all()
    )
    if not store_ids:
        return []
    return list(
        db.scalars(
            select(StoreConnection).where(
                StoreConnection.tenant_id == actor.tenant_id, StoreConnection.id.in_(store_ids)
            )
        ).all()
    )


def can_write_store(db: Session, actor: Actor, store_id: str) -> bool:
    if actor.role in (Role.owner, Role.admin):
        return True
    access = db.scalar(
        select(UserStoreAccess).where(UserStoreAccess.user_id == actor.user_id, UserStoreAccess.store_id == store_id)
    )
    return bool(access and access.can_write)

