"""
LangChain tools wrapping Pipedream remote MCP tool definitions.

Tool names are prefixed with `pipedream__` to avoid collisions with Shopify tools.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, create_model

from app.pipedream.mcp_session import PipedreamMCPSession

_log = logging.getLogger(__name__)

_IDENT_RE = re.compile(r"[^a-zA-Z0-9_]+")


def _sanitize_lc_tool_name(raw: str) -> str:
    """LangChain / OpenAI tool names: alphanumeric + underscore."""
    s = _IDENT_RE.sub("_", (raw or "").strip())
    s = s.strip("_") or "tool"
    if s[0].isdigit():
        s = "t_" + s
    return s[:80]


def _json_prop_to_annotation(spec: dict[str, Any], *, required: bool, prop_key: str) -> tuple[Any, Any]:
    t = spec.get("type")
    if t == "integer":
        ann: Any = int
        fld = Field(..., validation_alias=prop_key) if required else Field(default=None, validation_alias=prop_key)
        if not required:
            ann = int | None
        return (ann, fld)
    if t == "number":
        ann = float
        fld = Field(..., validation_alias=prop_key) if required else Field(default=None, validation_alias=prop_key)
        if not required:
            ann = float | None
        return (ann, fld)
    if t == "boolean":
        ann = bool
        fld = Field(..., validation_alias=prop_key) if required else Field(default=None, validation_alias=prop_key)
        if not required:
            ann = bool | None
        return (ann, fld)
    if t == "array":
        ann = list[Any]
        fld = (
            Field(..., validation_alias=prop_key)
            if required
            else Field(default=None, validation_alias=prop_key)
        )
        if not required:
            ann = list[Any] | None
        return (ann, fld)
    if t == "object":
        ann = dict[str, Any]
        fld = (
            Field(..., validation_alias=prop_key)
            if required
            else Field(default=None, validation_alias=prop_key)
        )
        if not required:
            ann = dict[str, Any] | None
        return (ann, fld)
    ann = str
    fld = Field(..., validation_alias=prop_key) if required else Field(default=None, validation_alias=prop_key)
    if not required:
        ann = str | None
    return (ann, fld)


def _sanitize_field_name(name: str) -> str:
    s = _IDENT_RE.sub("_", name.strip())
    if not s or s[0].isdigit():
        s = "f_" + (s or "arg")
    return s


def _input_schema_to_model(tool_name: str, schema: dict[str, Any] | None) -> type[BaseModel]:
    """Build a Pydantic model from a JSON Schema object (minimal subset)."""
    if not schema or schema.get("type") != "object":
        class EmptyPdToolArgs(BaseModel):
            model_config = ConfigDict(extra="forbid")

        return EmptyPdToolArgs

    props: dict[str, Any] = schema.get("properties") or {}
    required: set[str] = set(schema.get("required") or [])
    model_name = "PdArgs_" + _sanitize_lc_tool_name(tool_name)[:40]
    fields: dict[str, tuple[Any, Any]] = {}
    used: set[str] = set()

    for prop_key, spec in props.items():
        if not isinstance(spec, dict):
            continue
        fname = _sanitize_field_name(str(prop_key))
        base = fname
        n = 2
        while fname in used:
            fname = f"{base}_{n}"
            n += 1
        used.add(fname)
        is_req = prop_key in required
        ann, fld = _json_prop_to_annotation(spec, required=is_req, prop_key=str(prop_key))
        fields[fname] = (ann, fld)

    if not fields:
        class EmptyPdToolArgs2(BaseModel):
            model_config = ConfigDict(extra="forbid")

        return EmptyPdToolArgs2

    return create_model(model_name, **fields)  # type: ignore[call-overload]


def build_pipedream_tools(
    session: PipedreamMCPSession | None,
    *,
    max_tools: int,
) -> list[Any]:
    """
    List tools from the Pipedream MCP server and wrap each as a StructuredTool.

    When session is None or not alive, returns an empty list.
    """
    if session is None or not session.is_alive():
        return []

    mcp_tools = session.list_mcp_tools()
    if not mcp_tools:
        _log.info("pipedream_tools: list_mcp_tools returned empty")
        return []

    cap = max(1, min(int(max_tools), 200))
    if len(mcp_tools) > cap:
        _log.warning("pipedream_tools: capping tools %d -> %d", len(mcp_tools), cap)
        mcp_tools = mcp_tools[:cap]

    out: list[Any] = []
    used_names: set[str] = set()

    for t in mcp_tools:
        orig_name = getattr(t, "name", "") or ""
        if not orig_name:
            continue
        base_lc = "pipedream__" + _sanitize_lc_tool_name(orig_name)
        lc_name = base_lc
        n = 2
        while lc_name in used_names:
            lc_name = f"{base_lc}_{n}"
            n += 1
        used_names.add(lc_name)

        desc = (getattr(t, "description", None) or f"Pipedream MCP tool `{orig_name}`.").strip()
        raw_schema = getattr(t, "inputSchema", None)
        input_schema: dict[str, Any] | None = raw_schema if isinstance(raw_schema, dict) else None
        ArgsModel = _input_schema_to_model(orig_name, input_schema)

        def _make_caller(mcp_tool_name: str, Model: type[BaseModel]) -> Any:
            def _run(**kwargs: Any) -> str:
                bound = Model(**kwargs)
                api_args = bound.model_dump(by_alias=True, exclude_none=True)
                payload = session.call_tool(mcp_tool_name, api_args)
                return json.dumps(payload, default=str)

            return _run

        try:
            st = StructuredTool.from_function(
                name=lc_name,
                description=desc[:8000],
                func=_make_caller(orig_name, ArgsModel),
                args_schema=ArgsModel,
            )
            out.append(st)
        except Exception as e:  # noqa: BLE001
            _log.warning("pipedream_tools: skip tool %s: %s", orig_name, e)

    _log.info("pipedream_tools: registered %d structured tools", len(out))
    return out
