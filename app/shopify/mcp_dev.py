"""
Runtime client for Shopify Dev MCP (@shopify/dev-mcp) via stdio.

See https://shopify.dev/docs/apps/build/devmcp — the server is started with
`npx -y @shopify/dev-mcp@latest` and exposes tools such as `learn_shopify_api`
and `introspect_graphql_schema` for documentation and schema discovery.

Requires Python >= 3.10 (mcp package) and Node.js 18+ with `npx` on PATH.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from typing import Any

_log = logging.getLogger(__name__)

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.types import TextContent

    _MCP_IMPORT_OK = True
    _IMPORT_ERR: str | None = None
except ImportError as e:  # pragma: no cover
    ClientSession = Any  # type: ignore[misc, assignment]
    StdioServerParameters = Any  # type: ignore[misc, assignment]
    stdio_client = Any  # type: ignore[misc, assignment]
    TextContent = Any  # type: ignore[misc, assignment]
    _MCP_IMPORT_OK = False
    _IMPORT_ERR = str(e)


def mcp_sdk_available() -> bool:
    return _MCP_IMPORT_OK


def mcp_import_error() -> str | None:
    if _MCP_IMPORT_OK:
        return None
    return _IMPORT_ERR or "mcp package not installed"


def _call_tool_result_to_payload(result: Any) -> dict[str, Any]:
    """Flatten MCP CallToolResult into a small JSON-friendly dict for the LLM."""
    if getattr(result, "isError", False):
        texts: list[str] = []
        for block in getattr(result, "content", []) or []:
            if isinstance(block, TextContent):
                texts.append(block.text)
            elif isinstance(block, dict) and block.get("type") == "text":
                texts.append(str(block.get("text", "")))
            else:
                texts.append(str(block))
        return {"ok": False, "isError": True, "message": "\n".join(texts).strip() or "MCP tool error"}

    parts: list[str] = []
    for block in getattr(result, "content", []) or []:
        if isinstance(block, TextContent):
            parts.append(block.text)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        else:
            parts.append(str(block))
    text = "\n".join(parts).strip()
    return {"ok": True, "text": text}


class ShopifyDevMCPSession:
    """
    One stdio MCP session (one Node subprocess) for the lifetime of a chat agent run.
    Thread: dedicated asyncio loop blocked on shutdown event so run_coroutine_threadsafe works.
    """

    def __init__(
        self,
        *,
        command: str = "npx",
        args: list[str] | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        if not _MCP_IMPORT_OK:
            raise RuntimeError(f"Python MCP SDK not available: {mcp_import_error()}")
        self._command = command
        self._args = args if args is not None else ["-y", "@shopify/dev-mcp@latest"]
        self._extra_env = extra_env or {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session: ClientSession | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._ready = threading.Event()
        self._start_error: list[BaseException | None] = [None]

    def start(self, *, timeout_s: float = 120.0) -> None:
        if self._thread and self._thread.is_alive():
            return

        def target() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            self._shutdown_event = asyncio.Event()

            async def runner() -> None:
                env = {**os.environ, **self._extra_env}
                params = StdioServerParameters(
                    command=self._command,
                    args=self._args,
                    env=env,
                )
                try:
                    async with stdio_client(params) as streams:
                        read, write = streams
                        async with ClientSession(read, write) as session:
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

        self._thread = threading.Thread(target=target, name="shopify-dev-mcp", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=timeout_s):
            raise TimeoutError("Timed out waiting for Shopify Dev MCP to initialize (check Node/npx).")
        err = self._start_error[0]
        if err is not None:
            raise RuntimeError(f"Shopify Dev MCP failed to start: {err}") from err
        if self._session is None:
            raise RuntimeError("Shopify Dev MCP session is None after start.")

    def close(self) -> None:
        if self._loop is None or self._shutdown_event is None:
            return
        loop = self._loop
        ev = self._shutdown_event

        def _set() -> None:
            ev.set()

        loop.call_soon_threadsafe(_set)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=45.0)
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

    def call_tool(self, name: str, arguments: dict[str, Any], *, timeout_s: float = 120.0) -> dict[str, Any]:
        if self._loop is None or self._session is None:
            return {"ok": False, "error": "MCP session not started"}
        sess = self._session
        loop = self._loop

        async def _call() -> dict[str, Any]:
            assert sess is not None
            raw = await sess.call_tool(name, arguments)
            return _call_tool_result_to_payload(raw)

        fut = asyncio.run_coroutine_threadsafe(_call(), loop)
        try:
            return fut.result(timeout=timeout_s)
        except Exception as e:  # noqa: BLE001
            _log.warning("MCP tool %s failed: %s", name, e)
            return {"ok": False, "error": str(e)}


def try_start_shopify_dev_mcp(
    *,
    command: str,
    args: list[str],
    extra_env: dict[str, str] | None = None,
) -> ShopifyDevMCPSession | None:
    """Start MCP or return None on failure (logged)."""
    if not _MCP_IMPORT_OK:
        _log.warning("Shopify Dev MCP disabled: %s", mcp_import_error())
        return None
    try:
        s = ShopifyDevMCPSession(command=command, args=args, extra_env=extra_env)
        s.start()
        return s
    except Exception as e:  # noqa: BLE001
        _log.warning("Could not start Shopify Dev MCP subprocess: %s", e)
        return None


def parse_mcp_args(arg_string: str) -> list[str]:
    """Split env-style args string into list (handles simple quoted segments)."""
    s = (arg_string or "").strip()
    if not s:
        return ["-y", "@shopify/dev-mcp@latest"]
    parts: list[str] = []
    cur: list[str] = []
    in_q: str | None = None
    for ch in s:
        if in_q:
            if ch == in_q:
                in_q = None
            else:
                cur.append(ch)
        elif ch in "\"'":
            in_q = ch
        elif ch.isspace():
            if cur:
                parts.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur))
    return parts if parts else ["-y", "@shopify/dev-mcp@latest"]


def env_block_for_shopify_mcp(settings: Any) -> dict[str, str]:
    """Instrumentation / validation env from settings."""
    out: dict[str, str] = {}
    if getattr(settings, "shopify_dev_mcp_opt_out_instrumentation", False):
        out["OPT_OUT_INSTRUMENTATION"] = "true"
    mode = getattr(settings, "shopify_dev_mcp_liquid_validation_mode", None)
    if mode:
        out["LIQUID_VALIDATION_MODE"] = str(mode)
    return out
