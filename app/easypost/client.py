"""
Thin EasyPost REST client (Basic auth: API key as username, empty password).

See https://docs.easypost.com/
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

_log = logging.getLogger(__name__)


class EasyPostClient:
    def __init__(self, *, api_key: str, base_url: str = "https://api.easypost.com/v2") -> None:
        self._auth = (api_key.strip(), "")
        self._base = base_url.strip().rstrip("/")

    def _check(self, resp: httpx.Response) -> dict[str, Any]:
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            resp.raise_for_status()
            raise RuntimeError(f"EasyPost non-JSON response: {resp.text[:300]}")
        if not resp.is_success:
            msg = self._format_error(data)
            raise RuntimeError(f"EasyPost HTTP {resp.status_code}: {msg}")
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(f"EasyPost error: {self._format_error(data)}")
        return data

    @staticmethod
    def _format_error(data: dict[str, Any]) -> str:
        err = data.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("code") or err)
        return str(err)

    def get_shipment(self, shipment_id: str) -> dict[str, Any]:
        with httpx.Client(timeout=60.0, auth=self._auth) as client:
            r = client.get(f"{self._base}/shipments/{shipment_id}")
        data = self._check(r)
        return data["shipment"]

    def create_shipment(
        self,
        *,
        to_address: dict[str, Any],
        from_address: dict[str, Any],
        parcel: dict[str, Any],
        reference: Optional[str] = None,
    ) -> dict[str, Any]:
        shipment: dict[str, Any] = {
            "to_address": to_address,
            "from_address": from_address,
            "parcel": parcel,
        }
        if reference:
            shipment["reference"] = reference
        body = {"shipment": shipment}
        with httpx.Client(timeout=60.0, auth=self._auth) as client:
            r = client.post(f"{self._base}/shipments", json=body)
        data = self._check(r)
        return data["shipment"]

    def buy_shipment(self, shipment_id: str, rate_id: str) -> dict[str, Any]:
        body = {"rate": {"id": rate_id.strip()}}
        with httpx.Client(timeout=60.0, auth=self._auth) as client:
            r = client.post(f"{self._base}/shipments/{shipment_id}/buy", json=body)
        data = self._check(r)
        return data["shipment"]

    def refund_shipment(self, shipment_id: str) -> dict[str, Any]:
        with httpx.Client(timeout=60.0, auth=self._auth) as client:
            r = client.post(f"{self._base}/shipments/{shipment_id}/refund", json={})
        data = self._check(r)
        return data["shipment"]
