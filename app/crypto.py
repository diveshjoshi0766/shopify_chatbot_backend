from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.settings import get_settings


def _fernet() -> Fernet:
    key_str = get_settings().encryption_key_base64
    if not key_str:
        raise RuntimeError("ENCRYPTION_KEY_BASE64 is not configured")
    key = key_str.encode("utf-8")
    return Fernet(key)


def encrypt_str(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_str(value_enc: str) -> str:
    try:
        return _fernet().decrypt(value_enc.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        raise ValueError("Invalid encryption token") from e

