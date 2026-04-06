from __future__ import annotations

import mongomock

from app.mongo_repository import MongoRepository, ensure_mongo_indexes


def make_test_repository() -> MongoRepository:
    client = mongomock.MongoClient()
    coll = client["test_app"]["dyspensr_ai_bot"]
    ensure_mongo_indexes(coll)
    return MongoRepository(coll)
