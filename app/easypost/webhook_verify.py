"""
Validate EasyPost webhook HMAC (X-Hmac-Signature).

Matches easypost-python ``validate_webhook``: digest is ``hmac-sha256-hex=`` + lowercase hex
of HMAC-SHA256(secret, raw_body), with secret normalized via NFKD.
"""
from __future__ import annotations

import hashlib
import hmac
import unicodedata


def easypost_webhook_signature_valid(*, secret: str, raw_body: bytes, signature_header: str | None) -> bool:
    if not secret or not signature_header or not raw_body:
        return False
    normalized_secret = unicodedata.normalize("NFKD", secret)
    encoded_secret = bytes(normalized_secret, "utf8")
    expected_sig = hmac.new(key=encoded_secret, msg=raw_body, digestmod=hashlib.sha256)
    digest = "hmac-sha256-hex=" + expected_sig.hexdigest()
    return hmac.compare_digest(digest, signature_header.strip())
