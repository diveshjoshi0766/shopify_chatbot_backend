"""
Ensures the admin user referenced by AUTH_ADMIN_REGISTER_* can sign in.

Those env vars gate POST /auth/register; this module additionally creates that
user on API startup (or sets password_hash if the user exists without one) so
POST /auth/login works for the first admin without a manual seed step.
"""
from __future__ import annotations

import logging

from pymongo.errors import DuplicateKeyError

from app.db import get_tool_repository
from app.models import Role
from app.passwords import hash_password
from app.settings import get_settings

_log = logging.getLogger(__name__)


def ensure_bootstrap_admin_user() -> None:
    settings = get_settings()
    email = (settings.auth_admin_register_email or "").strip().lower()
    raw_pw = settings.auth_admin_register_password or ""
    tenant_id = (settings.default_tenant_id or "").strip()
    if not email or not raw_pw or not tenant_id:
        return

    db = get_tool_repository()
    try:
        if not db.get_tenant(tenant_id):
            try:
                db.insert_tenant(tenant_id=tenant_id, name=tenant_id)
            except DuplicateKeyError:
                pass

        existing = db.get_user_by_tenant_email(tenant_id, email)
        if existing is None:
            try:
                db.insert_user(
                    tenant_id=tenant_id,
                    email=email,
                    display_name=None,
                    password_hash=hash_password(raw_pw),
                    role=Role.admin,
                )
            except DuplicateKeyError:
                _log.warning("Bootstrap admin insert raced; another process may have created the user")
                return
            _log.info(
                "Bootstrap admin user created for tenant %s (matches AUTH_ADMIN_REGISTER_EMAIL)",
                tenant_id,
            )
            return

        if not existing.password_hash:
            db.update_user(existing.id, {"password_hash": hash_password(raw_pw)})
            _log.info(
                "Bootstrap: set login password for existing user %s (had no password_hash)",
                email,
            )
    except Exception:  # noqa: BLE001
        _log.exception("Bootstrap admin user failed — login may still require manual registration")
