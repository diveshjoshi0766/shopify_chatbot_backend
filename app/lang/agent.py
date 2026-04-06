"""
LangGraph ReAct agent for Shopify Admin operations.

Accepts an external MCP session (singleton, managed by lifespan) and
a LangGraph checkpointer for multi-turn conversation memory.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent
from app.authz import Actor
from app.mongo_repository import MongoRepository
from app.settings import get_settings
from app.shopify.mcp_dev import ShopifyDevMCPSession
from app.shopify.tools import build_shopify_tools

_log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a Shopify Admin assistant using LangChain tools against the merchant's store.\n\n"
    "TOOL USAGE RULES — follow strictly:\n"
    "1. For simple data queries (products, orders, customers): go DIRECTLY to `admin_search_products`, "
    "`admin_get_order`, or `shopify_admin_graphql`. Do NOT call MCP tools for routine data fetches.\n"
    "2. `shopify_admin_graphql` is READ-ONLY (queries only). For ANY mutation / write, use "
    "`propose_shopify_admin_mutation` (or a specific propose_* tool) so the user confirms first.\n"
    "3. Shopify Dev MCP tools (`shopify_dev_*`) are for API DOCUMENTATION and SCHEMA DISCOVERY only. "
    "Use them ONLY when you genuinely don't know the correct query/mutation name, field names, or required scopes. "
    "When using MCP: call `shopify_dev_learn_api` once, then ONE call to `shopify_dev_introspect_graphql_schema` "
    "or `shopify_dev_search_docs_chunks`, then STOP searching and answer with what you found. "
    "NEVER call the same MCP tool more than twice — synthesize your answer from what you have.\n"
    "4. If unsure which store is in scope, call `list_scoped_stores`.\n"
    "5. Prefer specialized tools: `admin_search_products`, `admin_get_order`, propose_* for writes.\n"
    "6. Always answer concisely after gathering information. Do not keep searching endlessly."
)


@dataclass(frozen=True)
class AgentResult:
    text: str
    tool_calls: list[dict[str, Any]]


def _message_content_to_str(msg: BaseMessage) -> str:
    content = getattr(msg, "content", None)
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text" and "text" in block:
                    parts.append(str(block["text"]))
                elif "text" in block:
                    parts.append(str(block["text"]))
            else:
                parts.append(str(block))
        return "".join(parts)
    return str(content)


def _llm() -> ChatOpenAI:
    settings = get_settings()
    key = (settings.openai_api_key or os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Add it to backend/.env and restart the API."
        )
    return ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=key)


def _tool_call_names(tool_calls: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for tc in tool_calls:
        if isinstance(tc, dict) and tc.get("name"):
            names.append(str(tc["name"]))
        elif hasattr(tc, "name"):
            names.append(str(getattr(tc, "name", "")))
    return [n for n in names if n]


def run_agent(
    db: MongoRepository,
    *,
    actor: Actor,
    store_ids: list[str],
    user_message: str,
    conversation_id: str,
    mcp_session: Optional[ShopifyDevMCPSession] = None,
    checkpointer: Optional[BaseCheckpointSaver] = None,
) -> AgentResult:
    """
    Runs a LangGraph ReAct agent with Shopify tools scoped to store_ids.

    `mcp_session` — singleton Shopify Dev MCP session (from app.state).
    `checkpointer` — LangGraph memory for multi-turn conversation continuity.
    """
    t0 = time.perf_counter()

    active_mcp = mcp_session if (mcp_session and mcp_session.is_alive()) else None
    _log.info(
        "agent_start tenant=%s user=%s conv=%s store_ids=%d mcp_attached=%s",
        actor.tenant_id,
        actor.user_id,
        conversation_id,
        len(store_ids),
        bool(active_mcp),
    )
    tools = build_shopify_tools(
        db,
        actor=actor,
        store_ids=store_ids,
        mcp_session=active_mcp,
        conversation_id=conversation_id,
    )

    agent = create_react_agent(
        _llm(),
        tools=tools,
        checkpointer=checkpointer,
        prompt=SYSTEM_PROMPT,
    )

    config = {
        "configurable": {"thread_id": f"{actor.tenant_id}:{actor.user_id}:{conversation_id}"},
        "recursion_limit": 28,
    }

    try:
        result = agent.invoke(
            {"messages": [HumanMessage(content=user_message)]},
            config=config,
        )
    except GraphRecursionError:
        _log.warning(
            "agent_recursion_limit tenant=%s user=%s stores=%d — returning partial result",
            actor.tenant_id, actor.user_id, len(store_ids),
        )
        snapshot = agent.get_state(config)
        messages = list(snapshot.values.get("messages", []))
        last_text = ""
        for m in reversed(messages):
            txt = _message_content_to_str(m)
            if txt.strip() and len(txt) > 20:
                last_text = txt
                break
        tool_calls: list[dict[str, Any]] = []
        for m in messages:
            if getattr(m, "tool_calls", None):
                tool_calls.extend(m.tool_calls)
        tnames = _tool_call_names(tool_calls)
        mcp_n = sum(1 for n in tnames if n.startswith("shopify_dev_"))
        admin_n = len(tnames) - mcp_n
        if not last_text:
            last_text = "I gathered some information but couldn't finalize an answer in time. Please try a more specific question."
        ms = (time.perf_counter() - t0) * 1000
        _log.info(
            "agent_done tenant=%s user=%s conv=%s stores=%d ms=%.0f phase=recursion_limit "
            "mcp_session_alive=%s tool_calls_total=%d mcp_tools=%d admin_tools=%d names=%s",
            actor.tenant_id,
            actor.user_id,
            conversation_id,
            len(store_ids),
            ms,
            bool(active_mcp),
            len(tnames),
            mcp_n,
            admin_n,
            tnames,
        )
        return AgentResult(text=last_text, tool_calls=tool_calls)

    messages = result.get("messages", [])
    last = _message_content_to_str(messages[-1]) if messages else ""
    tool_calls_out: list[dict[str, Any]] = []
    for m in messages:
        if getattr(m, "tool_calls", None):
            tool_calls_out.extend(m.tool_calls)
    names = _tool_call_names(tool_calls_out)
    mcp_n = sum(1 for n in names if n.startswith("shopify_dev_"))
    admin_n = len(names) - mcp_n
    ms = (time.perf_counter() - t0) * 1000
    _log.info(
        "agent_done tenant=%s user=%s conv=%s stores=%d ms=%.0f mcp_session_alive=%s "
        "tool_calls_total=%d mcp_tools=%d admin_tools=%d names=%s",
        actor.tenant_id,
        actor.user_id,
        conversation_id,
        len(store_ids),
        ms,
        bool(active_mcp),
        len(names),
        mcp_n,
        admin_n,
        names,
    )
    return AgentResult(text=last, tool_calls=tool_calls_out)

