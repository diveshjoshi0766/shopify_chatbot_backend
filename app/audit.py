from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.mongo_repository import MongoRepository


def audit(
    db: "MongoRepository",
    *,
    tenant_id: str,
    event_type: str,
    payload: dict,
    user_id: Optional[str] = None,
    store_id: Optional[str] = None,
) -> None:
    """Append an audit document (MongoDB writes immediately)."""
    db.insert_audit(
        tenant_id=tenant_id,
        user_id=user_id,
        store_id=store_id,
        event_type=event_type,
        payload=payload,
    )
