from __future__ import annotations

import logging
from typing import Generator, Optional

from pymongo import MongoClient
from pymongo.database import Database

from app.mongo_repository import MongoRepository, ensure_mongo_indexes
from app.settings import get_settings

_log = logging.getLogger(__name__)

_client: Optional[MongoClient] = None


def get_mongo_client() -> MongoClient:
    global _client
    if _client is None:
        settings = get_settings()
        _client = MongoClient(settings.mongodb_uri)
        _log.debug("MongoClient created")
    return _client


def get_mongo_database() -> Database:
    settings = get_settings()
    client = get_mongo_client()
    db_name = settings.resolved_mongo_database_name()
    return client[db_name]


def get_mongo_collection():
    settings = get_settings()
    return get_mongo_database()[settings.mongodb_collection]


def get_tool_repository() -> MongoRepository:
    """Thread-safe repo for LangGraph tool workers (shares process-wide client)."""
    return MongoRepository(get_mongo_collection())


def ensure_mongo_schema() -> None:
    coll = get_mongo_collection()
    ensure_mongo_indexes(coll)


def get_db() -> Generator[MongoRepository, None, None]:
    repo = MongoRepository(get_mongo_collection())
    try:
        yield repo
    finally:
        pass
