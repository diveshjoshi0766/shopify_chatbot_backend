from __future__ import annotations

TOOL_TO_REQUIRED_SCOPES: dict[str, list[str]] = {
    # Reads
    "admin_search_products": ["read_products"],
    "admin_get_order": ["read_orders"],
    # Writes (proposals executed on confirm)
    "propose_update_product_price": ["write_products"],
    "propose_update_inventory": ["write_inventory"],
    "propose_add_order_tag": ["write_orders"],
}

