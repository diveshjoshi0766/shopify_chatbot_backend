from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException

from app.auth import verify_access_token
from app.authz import Actor, get_actor
from app.db import get_db
from app.mongo_repository import MongoRepository
from app.settings import get_settings


def get_actor_from_headers(
    db: MongoRepository = Depends(get_db),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
) -> Actor:
    if not x_tenant_id or not x_user_id:
        raise HTTPException(status_code=401, detail="Missing X-Tenant-Id / X-User-Id")
    try:
        return get_actor(db, tenant_id=x_tenant_id, user_id=x_user_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e


def get_current_actor(
    db: MongoRepository = Depends(get_db),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
) -> Actor:
    settings = get_settings()

    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        try:
            claims = verify_access_token(token)
            return get_actor(db, tenant_id=claims.tenant_id, user_id=claims.user_id)
        except PermissionError as e:
            raise HTTPException(status_code=401, detail=str(e)) from e

    if settings.auth_allow_legacy_headers:
        return get_actor_from_headers(db=db, x_tenant_id=x_tenant_id, x_user_id=x_user_id)

    raise HTTPException(status_code=401, detail="Missing bearer token")
