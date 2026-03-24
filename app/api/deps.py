from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.authz import Actor, get_actor
from app.db import get_db


def get_actor_from_headers(
    db: Session = Depends(get_db),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
) -> Actor:
    if not x_tenant_id or not x_user_id:
        raise HTTPException(status_code=401, detail="Missing X-Tenant-Id / X-User-Id")
    try:
        return get_actor(db, tenant_id=x_tenant_id, user_id=x_user_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e

