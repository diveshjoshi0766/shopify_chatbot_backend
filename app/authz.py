from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException

from app.audit import audit
from app.models import Role, StoreConnection
from app.mongo_repository import MongoRepository


@dataclass(frozen=True)
class Actor:
    tenant_id: str
    user_id: str
    role: Role


def get_actor(db: MongoRepository, tenant_id: str, user_id: str) -> Actor:
    user = db.get_user(user_id)
    if not user or user.tenant_id != tenant_id:
        raise PermissionError("Unknown user")
    return Actor(tenant_id=tenant_id, user_id=user.id, role=user.role)


def list_accessible_stores(db: MongoRepository, actor: Actor) -> list[StoreConnection]:
    """All users in the tenant may use every connected store for read/chat scoping."""
    return db.list_stores_for_tenant(actor.tenant_id)


def can_write_store(db: MongoRepository, actor: Actor, store_id: str) -> bool:
    """
    Write tools and /chat/confirm check this only when a mutation is involved.

    Read-only users never pass; owner/admin/member may write on any store that belongs
    to their tenant (no per-store membership rows).
    """
    if actor.role == Role.read_only:
        return False
    if not db.get_stores_by_ids(actor.tenant_id, [store_id]):
        return False
    return actor.role in (Role.owner, Role.admin, Role.member)


def require_roles(actor: Actor, allowed: tuple[Role, ...], db: MongoRepository | None = None) -> None:
    if actor.role in allowed:
        return
    if db is not None:
        audit(
            db,
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            event_type="authz_deny",
            payload={"reason": "role_not_allowed", "role": actor.role.value, "allowed": [r.value for r in allowed]},
        )
    raise HTTPException(status_code=403, detail="Insufficient role permissions")


def require_store_write_access(db: MongoRepository, actor: Actor, store_id: str) -> None:
    if can_write_store(db, actor, store_id):
        return
    audit(
        db,
        tenant_id=actor.tenant_id,
        user_id=actor.user_id,
        store_id=store_id,
        event_type="authz_deny",
        payload={"reason": "no_store_write_access"},
    )
    raise HTTPException(status_code=403, detail=f"No write access for store {store_id}")
