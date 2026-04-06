"""
Executes confirmed pending actions against Shopify Admin GraphQL.

Normalizes REST-style numeric IDs to GID form and treats non-empty userErrors as failure.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.shopify.admin_client import ShopifyAdminClient


@dataclass(frozen=True)
class ExecutionResult:
    ok: bool
    store_id: str
    details: dict[str, Any]


def _to_shopify_gid(resource: str, raw: str) -> str:
    """Admin GraphQL IDs must be gid://shopify/{Resource}/{id}; accept plain numeric IDs."""
    s = (raw or "").strip()
    if not s:
        return s
    if s.lower().startswith("gid://"):
        return s
    if s.isdigit():
        return f"gid://shopify/{resource}/{s}"
    return s


def _raise_if_user_errors(node: Any) -> None:
    if not isinstance(node, dict):
        return
    errors = node.get("userErrors")
    if not isinstance(errors, list) or not errors:
        return
    parts: list[str] = []
    for e in errors:
        if isinstance(e, dict):
            parts.append(str(e.get("message") or e))
        else:
            parts.append(str(e))
    raise RuntimeError("Shopify: " + "; ".join(parts))


def _deep_raise_user_errors(obj: Any) -> None:
    """For generic_graphql_mutation payloads, fail on any nested userErrors."""
    if isinstance(obj, dict):
        _raise_if_user_errors(obj)
        for v in obj.values():
            _deep_raise_user_errors(v)
    elif isinstance(obj, list):
        for x in obj:
            _deep_raise_user_errors(x)


def execute_pending_action(
    *,
    client: ShopifyAdminClient,
    action_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if action_type == "update_product_price":
        variant_id = _to_shopify_gid("ProductVariant", str(payload["variant_id"]))
        price = str(payload["price"])
        gql = """
        mutation VariantUpdate($input: ProductVariantInput!) {
          productVariantUpdate(input: $input) {
            productVariant { id price }
            userErrors { field message }
          }
        }
        """
        data = client.graphql(gql, {"input": {"id": variant_id, "price": price}})
        out = data["productVariantUpdate"]
        _raise_if_user_errors(out)
        return out

    if action_type == "update_inventory":
        inventory_item_id = _to_shopify_gid("InventoryItem", str(payload["inventory_item_id"]))
        location_id = _to_shopify_gid("Location", str(payload["location_id"]))
        available = int(payload["available"])
        gql = """
        mutation SetOnHand($input: InventorySetOnHandQuantitiesInput!) {
          inventorySetOnHandQuantities(input: $input) {
            userErrors { field message }
            inventoryAdjustmentGroup { id }
          }
        }
        """
        variables = {
            "input": {
                "setQuantities": [
                    {"inventoryItemId": inventory_item_id, "locationId": location_id, "quantity": available}
                ]
            }
        }
        data = client.graphql(gql, variables)
        out = data["inventorySetOnHandQuantities"]
        _raise_if_user_errors(out)
        return out

    if action_type == "add_order_tag":
        order_id = _to_shopify_gid("Order", str(payload["order_id"]))
        tag = payload["tag"]
        gql = """
        mutation TagsAdd($id: ID!, $tags: [String!]!) {
          tagsAdd(id: $id, tags: $tags) {
            node { id }
            userErrors { field message }
          }
        }
        """
        data = client.graphql(gql, {"id": order_id, "tags": [tag]})
        out = data["tagsAdd"]
        _raise_if_user_errors(out)
        return out

    if action_type == "generic_graphql_mutation":
        gql = payload["graphql_document"]
        variables = payload.get("variables", {})
        data = client.graphql(gql, variables)
        _deep_raise_user_errors(data)
        return data

    raise ValueError(f"Unknown action_type: {action_type}")
