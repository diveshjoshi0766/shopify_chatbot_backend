"""
Per-request Pipedream remote MCP session over streamable HTTP.

Uses the same thread + asyncio loop pattern as Shopify Dev MCP stdio client.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from app.mcp_common import call_tool_result_to_payload
from app.pipedream.token_provider import PipedreamTokenProvider

_log = logging.getLogger(__name__)

try:
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client
    from mcp.types import PaginatedRequestParams, Tool

    _MCP_STREAM_OK = True
    _MCP_STREAM_ERR: str | None = None
except ImportError as e:  # pragma: no cover
    ClientSession = Any  # type: ignore[misc, assignment]
    streamable_http_client = Any  # type: ignore[misc, assignment]
    create_mcp_http_client = Any  # type: ignore[misc, assignment]
    PaginatedRequestParams = Any  # type: ignore[misc, assignment]
    Tool = Any  # type: ignore[misc, assignment]
    httpx = Any  # type: ignore[misc, assignment]
    _MCP_STREAM_OK = False
    _MCP_STREAM_ERR = str(e)


def streamable_mcp_client_available() -> bool:
    return _MCP_STREAM_OK


def streamable_mcp_import_error() -> str | None:
    return None if _MCP_STREAM_OK else (_MCP_STREAM_ERR or "streamable HTTP MCP client unavailable")


class PipedreamMCPSession:
    """
    One streamable-HTTP MCP session for the lifetime of a single chat request
    (start → agent tools → close).
    """

    def __init__(
        self,
        *,
        mcp_url: str,
        token_provider: PipedreamTokenProvider,
        project_id: str,
        environment: str,
        external_user_id: str,
        app_slug: str | None,
        app_discovery: bool,
        account_id: str | None = None,
        tool_mode: str | None = None,
    ) -> None:
        if not _MCP_STREAM_OK:
            raise RuntimeError(streamable_mcp_import_error())
        self._mcp_url = (mcp_url or "").strip().rstrip("/")
        self._token_provider = token_provider
        self._project_id = (project_id or "").strip()
        self._environment = (environment or "development").strip()
        self._external_user_id = external_user_id
        self._app_slug = (app_slug or "").strip() or None
        self._app_discovery = bool(app_discovery)
        self._account_id = (account_id or "").strip() or None
        self._tool_mode = (tool_mode or "").strip() or None

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session: ClientSession | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._ready = threading.Event()
        self._start_error: list[BaseException | None] = [None]

    def start(self, *, timeout_s: float = 120.0) -> None:
        if self._thread and self._thread.is_alive():
            return

        if not self._project_id:
            raise RuntimeError("Pipedream project_id is required")
        if not self._app_discovery and not self._app_slug:
            raise RuntimeError("Pipedream app slug is required unless app discovery is enabled")

        def target() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            self._shutdown_event = asyncio.Event()

            async def runner() -> None:
                try:
                    token = self._token_provider.get_token()
                except Exception as e:  # noqa: BLE001
                    self._start_error[0] = e
                    self._ready.set()
                    return

                headers: dict[str, str] = {
                    "Authorization": f"Bearer {token}",
                    "x-pd-project-id": self._project_id,
                    "x-pd-environment": self._environment,
                    "x-pd-external-user-id": self._external_user_id,
                }
                if self._app_discovery:
                    headers["x-pd-app-discovery"] = "true"
                else:
                    assert self._app_slug
                    headers["x-pd-app-slug"] = self._app_slug
                if self._account_id:
                    headers["x-pd-account-id"] = self._account_id
                if self._tool_mode:
                    headers["x-pd-tool-mode"] = self._tool_mode

                timeout = httpx.Timeout(60.0, read=300.0)
                try:
                    async with create_mcp_http_client(headers, timeout) as http_client:
                        async with streamable_http_client(
                            self._mcp_url,
                            http_client=http_client,
                            terminate_on_close=True,
                        ) as streams:
                            read_stream, write_stream, _get_id = streams
                            async with ClientSession(read_stream, write_stream) as session:
                                await session.initialize()
                                self._session = session
                                self._ready.set()
                                assert self._shutdown_event is not None
                                await self._shutdown_event.wait()
                except BaseException as e:
                    self._start_error[0] = e
                    self._ready.set()
                finally:
                    self._session = None

            try:
                loop.run_until_complete(runner())
            finally:
                loop.close()

        self._thread = threading.Thread(target=target, name="pipedream-mcp", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=timeout_s):
            raise TimeoutError("Timed out waiting for Pipedream MCP to initialize")
        err = self._start_error[0]
        if err is not None:
            raise RuntimeError(f"Pipedream MCP failed to start: {err}") from err
        if self._session is None:
            raise RuntimeError("Pipedream MCP session is None after start")

    def close(self) -> None:
        if self._loop is None or self._shutdown_event is None:
            return
        loop = self._loop
        ev = self._shutdown_event

        def _set() -> None:
            ev.set()

        loop.call_soon_threadsafe(_set)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=60.0)
        self._loop = None
        self._thread = None
        self._session = None
        self._shutdown_event = None

    def is_alive(self) -> bool:
        return (
            self._thread is not None
            and self._thread.is_alive()
            and self._session is not None
            and self._loop is not None
        )

    def list_mcp_tools(self, *, timeout_s: float = 120.0) -> list[Tool]:
        if self._loop is None or self._session is None:
            _log.warning("pipedream list_tools skipped: session not started")
            return []
        sess = self._session
        loop = self._loop

        async def _all() -> list[Tool]:
            assert sess is not None
            acc: list[Tool] = []
            cursor: str | None = None
            while True:
                if cursor:
                    result = await sess.list_tools(params=PaginatedRequestParams(cursor=cursor))
                else:
                    result = await sess.list_tools()
                acc.extend(result.tools)
                raw_next = result.nextCursor
                cursor = str(raw_next) if raw_next is not None else None
                if not cursor:
                    break
            return acc

        fut = asyncio.run_coroutine_threadsafe(_all(), loop)
        try:
            return fut.result(timeout=timeout_s)
        except Exception as e:  # noqa: BLE001
            _log.warning("Pipedream list_tools failed: %s", e)
            return []

    def call_tool(self, name: str, arguments: dict[str, Any], *, timeout_s: float = 120.0) -> dict[str, Any]:
        if self._loop is None or self._session is None:
            _log.warning("pipedream_mcp_tool_skip name=%s reason=session_not_started", name)
            return {"ok": False, "error": "Pipedream MCP session not started"}
        sess = self._session
        loop = self._loop
        _log.info(
            "pipedream_mcp_tool_start name=%s arg_keys=%s timeout_s=%s",
            name,
            sorted((arguments or {}).keys())[:20],
            timeout_s,
        )

        async def _call() -> dict[str, Any]:
            assert sess is not None
            raw = await sess.call_tool(name, arguments or {})
            return call_tool_result_to_payload(raw)

        fut = asyncio.run_coroutine_threadsafe(_call(), loop)
        try:
            out = fut.result(timeout=timeout_s)
            ok = bool(out.get("ok")) if isinstance(out, dict) else True
            preview = ""
            if isinstance(out, dict):
                if out.get("text"):
                    preview = str(out.get("text", ""))[:160].replace("\n", " ")
                elif out.get("message"):
                    preview = str(out.get("message", ""))[:160].replace("\n", " ")
            _log.info("pipedream_mcp_tool_done name=%s ok=%s preview=%s", name, ok, preview or "(none)")
            return out
        except Exception as e:  # noqa: BLE001
            _log.warning("Pipedream MCP tool %s failed: %s", name, e)
            return {"ok": False, "error": str(e)}
