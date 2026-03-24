from __future__ import annotations


DEFAULT_SCOPES_READ = [
    "read_products",
    "read_orders",
    "read_customers",
    "read_inventory",
]

DEFAULT_SCOPES_WRITE = [
    "write_products",
    "write_orders",
    "write_customers",
    "write_inventory",
]


def parse_scopes(scopes_csv: str) -> list[str]:
    parts = [p.strip() for p in scopes_csv.split(",")]
    return [p for p in parts if p]

