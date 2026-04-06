#!/usr/bin/env python3
"""
Create default dev identity: tenant `t1` and user `u1` (owner).
Run from the `backend/` directory:

    python scripts/seed_dev.py

Then the chat UI can use Tenant ID = t1 and User ID = u1.
"""
from __future__ import annotations

import sys
from pathlib import Path

_backend_root = Path(__file__).resolve().parent.parent
if str(_backend_root) not in sys.path:
    sys.path.insert(0, str(_backend_root))

from app.db import get_tool_repository
from app.models import Role


def main() -> None:
    db = get_tool_repository()
    if not db.get_tenant("t1"):
        db.insert_tenant(tenant_id="t1", name="dev-local")
        print("Created tenant t1")
    else:
        print("Tenant t1 already exists")

    if not db.get_user("u1"):
        db.insert_user(
            tenant_id="t1",
            email="dev@local.test",
            display_name=None,
            password_hash=None,
            role=Role.owner,
            user_id="u1",
        )
        print("Created user u1 (owner)")
    else:
        print("User u1 already exists")

    print("Done. Use Tenant ID t1 and User ID u1 in the chat UI.")


if __name__ == "__main__":
    main()
