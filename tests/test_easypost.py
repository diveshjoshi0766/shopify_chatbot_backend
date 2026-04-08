from __future__ import annotations

import hashlib
import hmac
import unicodedata

from app.easypost.webhook_verify import easypost_webhook_signature_valid
from app.lang.policy import check_write_policy
from tests.mongo_helpers import make_test_repository


def _sign_body(secret: str, body: bytes) -> str:
    normalized = unicodedata.normalize("NFKD", secret)
    key = bytes(normalized, "utf8")
    digest = hmac.new(key=key, msg=body, digestmod=hashlib.sha256).hexdigest()
    return "hmac-sha256-hex=" + digest


def test_easypost_webhook_hmac_valid() -> None:
    secret = "webhook-secret-1"
    body = b'{"id":"evt_test","description":"tracker.updated"}'
    sig = _sign_body(secret, body)
    assert easypost_webhook_signature_valid(secret=secret, raw_body=body, signature_header=sig) is True


def test_easypost_webhook_hmac_invalid() -> None:
    secret = "webhook-secret-1"
    body = b'{"id":"evt_test"}'
    assert (
        easypost_webhook_signature_valid(
            secret=secret,
            raw_body=body,
            signature_header="hmac-sha256-hex=deadbeef",
        )
        is False
    )


def test_easypost_pending_actions_allowlisted() -> None:
    assert check_write_policy("easypost_buy_label", {"shipment_id": "shp_x", "rate_id": "rate_x"}).allowed
    assert check_write_policy("easypost_refund_shipment", {"shipment_id": "shp_x"}).allowed


def test_easypost_webhook_event_idempotent() -> None:
    db = make_test_repository()
    assert db.insert_easypost_webhook_event_if_new(
        event_id="evt_dup",
        description="test",
        result_object="Shipment",
    )
    assert not db.insert_easypost_webhook_event_if_new(
        event_id="evt_dup",
        description="test",
        result_object="Shipment",
    )


def test_health_lists_easypost() -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app())
    r = client.get("/health")
    assert r.status_code == 200
    ep = r.json()["integrations"].get("easypost")
    assert ep is not None
    assert "api_key_configured" in ep
