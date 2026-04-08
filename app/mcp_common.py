"""
Shared helpers for MCP client tool results (stdio and streamable HTTP).

Used by Shopify Dev MCP (`app.shopify.mcp_dev`) and Pipedream remote MCP
(`app.pipedream.mcp_session`). Depends on optional `mcp.types.TextContent` when installed.
"""
from __future__ import annotations

from typing import Any

try:
    from mcp.types import TextContent

    _HAS_MCP = True
except ImportError:  # pragma: no cover
    TextContent = Any  # type: ignore[misc, assignment]
    _HAS_MCP = False


def call_tool_result_to_payload(result: Any) -> dict[str, Any]:
    """Flatten MCP CallToolResult into a small JSON-friendly dict for the LLM."""
    if getattr(result, "isError", False):
        texts: list[str] = []
        for block in getattr(result, "content", []) or []:
            if _HAS_MCP and isinstance(block, TextContent):
                texts.append(block.text)
            elif isinstance(block, dict) and block.get("type") == "text":
                texts.append(str(block.get("text", "")))
            else:
                texts.append(str(block))
        return {"ok": False, "isError": True, "message": "\n".join(texts).strip() or "MCP tool error"}

    parts: list[str] = []
    for block in getattr(result, "content", []) or []:
        if _HAS_MCP and isinstance(block, TextContent):
            parts.append(block.text)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        else:
            parts.append(str(block))
    text = "\n".join(parts).strip()
    return {"ok": True, "text": text}
