from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.mongo_repository import MongoRepository

from langchain_core.tools import tool

from app.audit import audit
from app.authz import Actor, can_write_store
from app.db import get_tool_repository
from app.lang.policy import check_write_policy
from app.models import StoreConnection
from app.shopify.admin_client import ShopifyAdminClient, ShopifyAdminSession
from app.shopify.mcp_dev import ShopifyDevMCPSession
from app.shopify.token_store import get_access_token_for_store

_log = logging.getLogger(__name__)

_GQL_COMMENT_RE = re.compile(r"#[^\n]*")


def _truncate(s: str, n: int = 220) -> str:
    s = s.replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 3] + "..."


def _is_graphql_mutation(doc: str) -> bool:
    """Return True if the GraphQL document declares a mutation operation."""
    stripped = _GQL_COMMENT_RE.sub("", doc).strip()
    return stripped.lower().startswith("mutation")


# Live Admin API schema discovery (merchant-specific; complements Shopify Dev MCP schema tools).
_INTROSPECT_TYPE_GQL = """
query IntrospectShopifyType($name: String!) {
  __type(name: $name) {
    name
    kind
    description
    fields {
      name
      description
      args {
        name
        type { name kind ofType { name kind ofType { name kind ofType { name kind } } } }
      }
      type { name kind ofType { name kind ofType { name kind ofType { name kind } } } }
    }
  }
}
"""


def _cap_introspection_payload(data: dict[str, Any], *, max_fields: int) -> dict[str, Any]:
    """Keep introspection responses bounded for LLM context."""
    t = data.get("__type")
    if not isinstance(t, dict):
        return data
    fields = t.get("fields")
    if isinstance(fields, list) and len(fields) > max_fields:
        t = {**t, "fields": fields[:max_fields], "_truncated_fields": len(fields) - max_fields}
        return {"__type": t, "_note": f"fields truncated to first {max_fields}; refine type_name or increase max_fields."}
    return data


def build_shopify_tools(
    db: "MongoRepository",
    *,
    actor: Actor,
    store_ids: list[str],
    mcp_session: ShopifyDevMCPSession | None = None,
    conversation_id: str | None = None,
) -> list[Any]:
    """
    Tool registry for the agent.

    When `mcp_session` is set, tools call Shopify Dev MCP (see https://shopify.dev/docs/apps/build/devmcp)
    for docs and official GraphQL schema discovery; store data still uses Admin API tools below.
    """

    stores = db.get_stores_by_ids(actor.tenant_id, store_ids)
    store_by_id = {s.id: s for s in stores}

    def _admin_client(store_id: str) -> ShopifyAdminClient:
        store = store_by_id.get(store_id)
        if not store:
            raise ValueError("Store not found in scope")
        token = get_access_token_for_store(store)
        return ShopifyAdminClient(ShopifyAdminSession(shop_domain=store.shop_domain, access_token=token))

    @tool
    def list_scoped_stores() -> list[dict[str, str]]:
        """List store_id + shop_domain currently scoped for this agent run."""
        _log.info("tool list_scoped_stores store_count=%d", len(stores))
        return [{"store_id": s.id, "shop_domain": s.shop_domain} for s in stores]

    @tool
    def shopify_admin_introspect_type(
        type_name: str,
        max_fields: int = 80,
    ) -> list[dict[str, Any]]:
        """
        Discover real Admin GraphQL fields for a type on this shop (live introspection).
        Use BEFORE writing unfamiliar `shopify_admin_graphql` queries so field names match the API.
        Common names: `Query` (root), `Product`, `Order`, `ProductVariant`, `Mutation`.
        Use for **live** field names against this shop's Admin API. Prefer `shopify_dev_introspect_graphql_schema`
        (MCP) for official schema/docs when unsure; use this when MCP is unavailable or you need shop-specific types.
        """
        tn = (type_name or "").strip()
        if not tn:
            return [{"ok": False, "error": "type_name is required"}]
        mf = max(5, min(int(max_fields or 80), 200))
        _log.info("tool shopify_admin_introspect_type type=%s max_fields=%d", tn, mf)
        out: list[dict[str, Any]] = []
        for sid in store_ids:
            try:
                raw = _admin_client(sid).graphql(_INTROSPECT_TYPE_GQL, {"name": tn})
                capped = _cap_introspection_payload(raw, max_fields=mf)
                out.append({"store_id": sid, "ok": True, "data": capped})
            except Exception as e:  # noqa: BLE001
                _log.warning("shopify_admin_introspect_type failed store_id=%s err=%s", sid, e)
                out.append({"store_id": sid, "ok": False, "error": str(e)})
        return out

    @tool
    def shopify_admin_graphql(graphql_document: str, variables_json: str = "{}") -> list[dict[str, Any]]:
        """
        Execute Shopify Admin GraphQL **queries** (read-only) on each scoped store.
        Mutations are NOT allowed here — use `propose_shopify_admin_mutation` for any write operation
        so the user can review and confirm before execution.
        If you are unsure of field or argument names, call `shopify_admin_introspect_type` first.
        `variables_json` must be a JSON object string, e.g. "{}" or '{"id": "gid://shopify/Product/123"}'.
        """
        if _is_graphql_mutation(graphql_document):
            return [{
                "ok": False,
                "error": (
                    "Mutations are blocked in shopify_admin_graphql for safety. "
                    "Use `propose_shopify_admin_mutation` instead so the user can confirm before execution."
                ),
            }]
        _log.info(
            "tool shopify_admin_graphql stores=%s doc=%s",
            store_ids,
            _truncate(graphql_document),
        )
        try:
            variables: dict[str, Any] = json.loads(variables_json) if variables_json.strip() else {}
            if not isinstance(variables, dict):
                return [{"ok": False, "error": "variables_json must be a JSON object"}]
        except json.JSONDecodeError as e:
            return [{"ok": False, "error": f"Invalid variables_json: {e}"}]
        out: list[dict[str, Any]] = []
        for sid in store_ids:
            try:
                data = _admin_client(sid).graphql(graphql_document, variables)
                out.append({"store_id": sid, "ok": True, "data": data})
            except Exception as e:  # noqa: BLE001
                _log.warning(
                    "tool shopify_admin_graphql failed store_id=%s error=%s",
                    sid,
                    str(e),
                )
                out.append({"store_id": sid, "ok": False, "error": str(e)})
        ok_n = sum(1 for row in out if row.get("ok"))
        _log.info(
            "tool shopify_admin_graphql done stores=%d ok=%d fail=%d",
            len(out),
            ok_n,
            len(out) - ok_n,
        )
        return out

    @tool
    def admin_search_products(query: str, first: int = 5) -> list[dict[str, Any]]:
        """Search products via Shopify Admin API."""
        _log.info("tool admin_search_products query=%s first=%d stores=%s", query, first, store_ids)
        out: list[dict[str, Any]] = []
        gql = """
        query Products($q: String!, $first: Int!) {
          products(first: $first, query: $q) {
            edges {
              node { id title handle status }
            }
          }
        }
        """
        for sid in store_ids:
            data = _admin_client(sid).graphql(gql, {"q": query, "first": first})
            edges = data["products"]["edges"]
            out.append(
                {
                    "store_id": sid,
                    "products": [e["node"] for e in edges],
                }
            )
        return out

    @tool
    def admin_get_order(order_id: str) -> list[dict[str, Any]]:
        """Get a single order by Admin GraphQL ID (gid://shopify/Order/...)."""
        _log.info("tool admin_get_order order_id=%s stores=%s", order_id, store_ids)
        gql = """
        query Order($id: ID!) {
          order(id: $id) {
            id
            name
            displayFinancialStatus
            displayFulfillmentStatus
            totalPriceSet { shopMoney { amount currencyCode } }
            customer { id email displayName }
            createdAt
          }
        }
        """
        out: list[dict[str, Any]] = []
        for sid in store_ids:
            data = _admin_client(sid).graphql(gql, {"id": order_id})
            out.append({"store_id": sid, "order": data.get("order")})
        return out

    def _create_pending_action(*, action_type: str, payload: dict, summary: str) -> dict[str, Any]:
        decision = check_write_policy(action_type, payload)
        if not decision.allowed:
            return {"ok": False, "reason": decision.reason}
        # LangGraph ToolNode runs tools in a thread pool; the FastAPI request Session is not
        # thread-safe and must not be used from tool workers (SQLite → OperationalError / 503).
        tool_db = get_tool_repository()
        try:
            for sid in store_ids:
                if not can_write_store(tool_db, actor, sid):
                    return {"ok": False, "reason": f"No write access for store {sid}"}
            pa = tool_db.insert_pending_action(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                conversation_id=conversation_id,
                store_ids=store_ids,
                action_type=action_type,
                tool_payload=payload,
                summary=summary,
            )
            audit(
                tool_db,
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                event_type="pending_action_create",
                payload={
                    "pending_action_id": pa.id,
                    "action_type": action_type,
                    "conversation_id": conversation_id,
                },
            )
            return {"ok": True, "pending_action_id": pa.id, "summary": summary}
        except Exception as e:  # noqa: BLE001
            _log.warning("pending_action_create failed: %s", e, exc_info=True)
            return {"ok": False, "error": str(e)}

    @tool
    def propose_update_product_price(variant_id: str, price: str) -> dict[str, Any]:
        """Propose updating a product variant price (executes on confirm)."""
        _log.info("tool propose_update_product_price variant_id=%s", variant_id)
        summary = f"Update variant {variant_id} price to {price} (scoped stores: {len(store_ids)})."
        return _create_pending_action(
            action_type="update_product_price",
            payload={"variant_id": variant_id, "price": price},
            summary=summary,
        )

    @tool
    def propose_update_inventory(inventory_item_id: str, available: int, location_id: str) -> dict[str, Any]:
        """Propose updating inventory levels (implementation executes on confirm)."""
        _log.info("tool propose_update_inventory inventory_item_id=%s", inventory_item_id)
        summary = (
            f"Set inventory_item {inventory_item_id} available={available} at location {location_id} "
            f"(scoped stores: {len(store_ids)})."
        )
        return _create_pending_action(
            action_type="update_inventory",
            payload={"inventory_item_id": inventory_item_id, "available": available, "location_id": location_id},
            summary=summary,
        )

    @tool
    def propose_add_order_tag(order_id: str, tag: str) -> dict[str, Any]:
        """Propose adding a tag to an order (implementation executes on confirm)."""
        _log.info("tool propose_add_order_tag order_id=%s", order_id)
        summary = f"Add tag '{tag}' to order {order_id} (scoped stores: {len(store_ids)})."
        return _create_pending_action(
            action_type="add_order_tag",
            payload={"order_id": order_id, "tag": tag},
            summary=summary,
        )

    @tool
    def propose_shopify_admin_mutation(graphql_document: str, variables_json: str = "{}", summary: str = "") -> dict[str, Any]:
        """
        Propose executing a Shopify Admin GraphQL **mutation** (write operation) on each scoped store.
        The mutation will NOT run immediately — the user must confirm first.
        Use this instead of `shopify_admin_graphql` for any mutation.
        `variables_json` must be a JSON object string. Provide a human-readable `summary` of what the mutation does.
        """
        if not _is_graphql_mutation(graphql_document):
            return {"ok": False, "error": "Only mutation operations are accepted. Use shopify_admin_graphql for queries."}
        try:
            variables: dict[str, Any] = json.loads(variables_json) if variables_json.strip() else {}
            if not isinstance(variables, dict):
                return {"ok": False, "error": "variables_json must be a JSON object"}
        except json.JSONDecodeError as e:
            return {"ok": False, "error": f"Invalid variables_json: {e}"}
        auto_summary = summary.strip() or f"Execute Admin GraphQL mutation on {len(store_ids)} store(s): {_truncate(graphql_document, 120)}"
        _log.info("tool propose_shopify_admin_mutation stores=%d doc=%s", len(store_ids), _truncate(graphql_document))
        return _create_pending_action(
            action_type="generic_graphql_mutation",
            payload={"graphql_document": graphql_document, "variables": variables},
            summary=auto_summary,
        )

    mcp_tools: list[Any] = []
    if mcp_session is not None:

        @tool
        def shopify_dev_learn_api(api: str, conversation_id: str = "") -> dict[str, Any]:
            """
            Shopify Dev MCP: call `learn_shopify_api` first when working with Shopify APIs (per Shopify).
            Use api like `admin` for Admin GraphQL. Pass conversation_id from a prior learn response when continuing.
            """
            args: dict[str, Any] = {"api": (api or "").strip()}
            if (conversation_id or "").strip():
                args["conversationId"] = conversation_id.strip()
            _log.info("tool shopify_dev_learn_api api=%s", args.get("api"))
            return mcp_session.call_tool("learn_shopify_api", args)

        @tool
        def shopify_dev_introspect_graphql_schema(
            query: str,
            api: str = "admin",
            filter_types: str = "",
        ) -> dict[str, Any]:
            """
            Shopify Dev MCP: explore official GraphQL schema (introspect_graphql_schema) before writing queries.
            `query` is a search string. `api` is usually `admin`. Optional filter_types: comma-separated
            all, types, queries, mutations.
            """
            arguments: dict[str, Any] = {"query": (query or "").strip(), "api": (api or "admin").strip()}
            ft = (filter_types or "").strip()
            if ft:
                arguments["filter"] = [x.strip() for x in ft.split(",") if x.strip()]
            _log.info("tool shopify_dev_introspect_graphql_schema api=%s", arguments.get("api"))
            return mcp_session.call_tool("introspect_graphql_schema", arguments)

        @tool
        def shopify_dev_search_docs_chunks(prompt: str, max_num_results: int = 8) -> dict[str, Any]:
            """Shopify Dev MCP: search shopify.dev documentation chunks (search_docs_chunks)."""
            _log.info("tool shopify_dev_search_docs_chunks max=%d", max_num_results)
            return mcp_session.call_tool(
                "search_docs_chunks",
                {"prompt": (prompt or "").strip(), "max_num_results": max(1, min(int(max_num_results or 8), 25))},
            )

        @tool
        def shopify_dev_fetch_full_docs(paths_json: str) -> dict[str, Any]:
            """
            Shopify Dev MCP: fetch full doc pages by path (fetch_full_docs). paths_json must be a JSON array of
            strings from learn_shopify_api / docs (e.g. [\"path/to/page\"]).
            """
            try:
                paths = json.loads(paths_json or "[]")
            except json.JSONDecodeError as e:
                return {"ok": False, "error": f"Invalid paths_json: {e}"}
            if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
                return {"ok": False, "error": "paths_json must be a JSON array of strings"}
            _log.info("tool shopify_dev_fetch_full_docs n_paths=%d", len(paths))
            return mcp_session.call_tool("fetch_full_docs", {"paths": paths})

        @tool
        def shopify_dev_validate_graphql_codeblocks(graphql_document: str, api: str = "admin") -> dict[str, Any]:
            """Shopify Dev MCP: validate GraphQL snippets against the official schema (validate_graphql_codeblocks)."""
            arguments: dict[str, Any] = {
                "codeblocks": [{"content": (graphql_document or "").strip()}],
                "api": (api or "admin").strip(),
            }
            _log.info("tool shopify_dev_validate_graphql_codeblocks api=%s", arguments.get("api"))
            return mcp_session.call_tool("validate_graphql_codeblocks", arguments)

        mcp_tools = [
            shopify_dev_learn_api,
            shopify_dev_introspect_graphql_schema,
            shopify_dev_search_docs_chunks,
            shopify_dev_fetch_full_docs,
            shopify_dev_validate_graphql_codeblocks,
        ]

    core_tools: list[Any] = [
        list_scoped_stores,
        shopify_admin_introspect_type,
        shopify_admin_graphql,
        admin_search_products,
        admin_get_order,
        propose_update_product_price,
        propose_update_inventory,
        propose_add_order_tag,
        propose_shopify_admin_mutation,
    ]
    return mcp_tools + core_tools

