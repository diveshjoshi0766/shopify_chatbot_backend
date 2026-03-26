from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models import AuditLog


def audit(
    db: Session,
    *,
    tenant_id: str,
    event_type: str,
    payload: dict,
    user_id: Optional[str] = None,
    store_id: Optional[str] = None,
) -> None:
    """Stage an audit row in the current transaction.

    Callers own transaction boundaries and should commit/rollback.
    """
    db.add(
        AuditLog(
            tenant_id=tenant_id,
            user_id=user_id,
            store_id=store_id,
            event_type=event_type,
            payload=payload,
        )
    )

