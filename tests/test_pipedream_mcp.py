from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import create_app
from app.mcp_common import call_tool_result_to_payload
from app.pipedream.token_provider import PipedreamTokenProvider


def test_pipedream_token_provider_not_configured() -> None:
    p = PipedreamTokenProvider(client_id="", client_secret="")
    assert p.configured() is False


def test_pipedream_token_provider_fetches_and_caches() -> None:
    p = PipedreamTokenProvider(client_id="cid", client_secret="sec")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"access_token": "test-access-token", "expires_in": 3600}
    mock_resp.raise_for_status = MagicMock()
    inst = MagicMock()
    inst.post.return_value = mock_resp
    cm = MagicMock()
    cm.__enter__.return_value = inst
    cm.__exit__.return_value = None

    with patch("app.pipedream.token_provider.httpx.Client", return_value=cm):
        t1 = p.get_token()
        t2 = p.get_token()

    assert t1 == "test-access-token"
    assert t2 == "test-access-token"
    assert inst.post.call_count == 1


def test_build_pipedream_tools_skips_without_session() -> None:
    from app.pipedream.tools import build_pipedream_tools

    assert build_pipedream_tools(None, max_tools=10) == []


def test_build_pipedream_tools_skips_dead_session() -> None:
    from app.pipedream.tools import build_pipedream_tools

    s = MagicMock()
    s.is_alive.return_value = False
    assert build_pipedream_tools(s, max_tools=10) == []


def test_call_tool_result_to_payload_ok_text() -> None:
    class R:
        isError = False
        content = [{"type": "text", "text": "hello"}]

    out = call_tool_result_to_payload(R())
    assert out["ok"] is True
    assert out["text"] == "hello"


def test_health_includes_pipedream_integration_block() -> None:
    client = TestClient(create_app())
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert "integrations" in body
    pd = body["integrations"].get("pipedream")
    assert pd is not None
    assert "enabled_setting" in pd
    assert "credentials_configured" in pd
    assert "streamable_mcp_available" in pd
