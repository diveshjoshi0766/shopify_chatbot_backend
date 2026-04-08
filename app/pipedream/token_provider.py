"""
OAuth client-credentials token for Pipedream remote MCP (Bearer).

Uses https://api.pipedream.com/v1/oauth/token as documented for MCP auth.
Caches the access token in memory with a short safety margin before expiry.
"""
from __future__ import annotations

import base64
import json
import logging
import threading
import time
from typing import Any, Optional

import httpx

_log = logging.getLogger(__name__)

PIPEDREAM_TOKEN_URL = "https://api.pipedream.com/v1/oauth/token"
_REFRESH_MARGIN_S = 90


def pipedream_sdk_installed() -> bool:
    try:
        import pipedream  # noqa: F401

        return True
    except ImportError:
        return False


def _jwt_exp_s(token: str) -> Optional[float]:
    """Return JWT exp as unix time if decodable (unverified)."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        raw = base64.urlsafe_b64decode(payload_b64.encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
        exp = data.get("exp")
        if isinstance(exp, (int, float)):
            return float(exp)
    except Exception:  # noqa: BLE001
        return None
    return None


class PipedreamTokenProvider:
    """Thread-safe cached bearer token for Pipedream MCP requests."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
    ) -> None:
        self._client_id = (client_id or "").strip()
        self._client_secret = (client_secret or "").strip()
        self._lock = threading.Lock()
        self._token: str | None = None
        self._refresh_after: float = 0.0
        self.last_error: str | None = None

    def configured(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def get_token(self) -> str:
        if not self.configured():
            raise RuntimeError("Pipedream client_id and client_secret are required")

        now = time.monotonic()
        with self._lock:
            if self._token and now < self._refresh_after:
                return self._token

            token, refresh_after_mono = self._fetch_token_unlocked()
            self._token = token
            self._refresh_after = refresh_after_mono
            self.last_error = None
            return self._token

    def _fetch_token_unlocked(self) -> tuple[str, float]:
        now_wall = time.time()
        now_mono = time.monotonic()
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    PIPEDREAM_TOKEN_URL,
                    json={
                        "grant_type": "client_credentials",
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                    },
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
                data: dict[str, Any] = response.json()
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
            _log.warning("Pipedream OAuth token fetch failed: %s", e)
            raise

        access = (data.get("access_token") or "").strip()
        if not access:
            self.last_error = "missing access_token in OAuth response"
            raise RuntimeError(self.last_error)

        expires_in = data.get("expires_in")
        ttl_s = float(expires_in) if isinstance(expires_in, (int, float)) else 3600.0
        ttl_s = max(120.0, ttl_s - _REFRESH_MARGIN_S)

        jwt_exp = _jwt_exp_s(access)
        if jwt_exp is not None:
            wall_ttl = jwt_exp - now_wall - _REFRESH_MARGIN_S
            if wall_ttl > 30:
                ttl_s = min(ttl_s, wall_ttl)

        refresh_after = now_mono + ttl_s
        _log.info("Pipedream OAuth token acquired, cache_ttl_s≈%.0f", ttl_s)
        return access, refresh_after
