from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from app.settings import get_settings


@dataclass(frozen=True)
class AuthClaims:
    tenant_id: str
    user_id: str
    exp: int


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * ((4 - (len(data) % 4)) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("utf-8"))


def issue_access_token(*, tenant_id: str, user_id: str) -> str:
    settings = get_settings()
    now = int(time.time())
    payload = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "iat": now,
        "exp": now + max(int(settings.auth_token_ttl_seconds), 60),
    }
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(
        settings.auth_token_secret.encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return f"{payload_b64}.{_b64url_encode(sig)}"


def verify_access_token(token: str) -> AuthClaims:
    settings = get_settings()
    try:
        payload_b64, sig_b64 = token.split(".", 1)
    except ValueError as e:
        raise PermissionError("Invalid token format") from e

    expected_sig = hmac.new(
        settings.auth_token_secret.encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    actual_sig = _b64url_decode(sig_b64)
    if not hmac.compare_digest(expected_sig, actual_sig):
        raise PermissionError("Invalid token signature")

    try:
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
        claims = AuthClaims(
            tenant_id=str(payload["tenant_id"]),
            user_id=str(payload["user_id"]),
            exp=int(payload["exp"]),
        )
    except Exception as e:  # noqa: BLE001
        raise PermissionError("Invalid token payload") from e

    if claims.exp <= int(time.time()):
        raise PermissionError("Token expired")
    return claims
