#!/usr/bin/env python3
"""
Create default dev identity: tenant `t1` and user `u1` (owner).
Run from the `backend/` directory:

    python scripts/seed_dev.py

Then the chat UI can use Tenant ID = t1 and User ID = u1.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow `python scripts/seed_dev.py` without PYTHONPATH=
_backend_root = Path(__file__).resolve().parent.parent
if str(_backend_root) not in sys.path:
    sys.path.insert(0, str(_backend_root))

from app.db import SessionLocal
from app.models import Role, Tenant, User


def main() -> None:
    db = SessionLocal()
    try:
        if not db.get(Tenant, "t1"):
            db.add(
                Tenant(
                    id="t1",
                    name="dev-local",
                    created_at=datetime.now(timezone.utc),
                )
            )
            print("Created tenant t1")
        else:
            print("Tenant t1 already exists")

        if not db.get(User, "u1"):
            db.add(
                User(
                    id="u1",
                    tenant_id="t1",
                    email="dev@local.test",
                    role=Role.owner,
                    created_at=datetime.now(timezone.utc),
                )
            )
            print("Created user u1 (owner)")
        else:
            print("User u1 already exists")

        db.commit()
        print("Done. Use Tenant ID t1 and User ID u1 in the chat UI.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
