"""Password hashing for user accounts (PBKDF2 via passlib; no bcrypt binary dependency)."""
from __future__ import annotations

from passlib.context import CryptContext

_pwd = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto", pbkdf2_sha256__default_rounds=290_000)


def hash_password(plain: str) -> str:
    return _pwd.hash(plain)


def verify_password(plain: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    return _pwd.verify(plain, password_hash)
