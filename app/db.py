from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.settings import get_settings


def _create_engine():
    settings = get_settings()
    url = settings.database_url
    connect_args: dict = {}
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
    kwargs: dict = {"pool_pre_ping": True, "connect_args": connect_args}
    if url.startswith("mysql"):
        kwargs["pool_recycle"] = 280
    return create_engine(url, **kwargs)


engine = _create_engine()
SessionLocal = sessionmaker(bind=engine, class_=Session, autocommit=False, autoflush=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

