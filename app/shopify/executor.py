from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.shopify.admin_client import ShopifyAdminClient


@dataclass(frozen=True)
class ExecutionResult:
    ok: bool
    store_id: str
    details: dict[str, Any]


def execute_pending_action(
    *,
    client: ShopifyAdminClient,
    action_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if action_type == "update_product_price":
        variant_id = payload["variant_id"]
        price = payload["price"]
        gql = """
        mutation VariantUpdate($input: ProductVariantInput!) {
          productVariantUpdate(input: $input) {
            productVariant { id price }
            userErrors { field message }
          }
        }
        """
        data = client.graphql(gql, {"input": {"id": variant_id, "price": price}})
        return data["productVariantUpdate"]

    if action_type == "update_inventory":
        inventory_item_id = payload["inventory_item_id"]
        location_id = payload["location_id"]
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
        return data["inventorySetOnHandQuantities"]

    if action_type == "add_order_tag":
        order_id = payload["order_id"]
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
        return data["tagsAdd"]

    if action_type == "generic_graphql_mutation":
        gql = payload["graphql_document"]
        variables = payload.get("variables", {})
        data = client.graphql(gql, variables)
        return data

    raise ValueError(f"Unknown action_type: {action_type}")

