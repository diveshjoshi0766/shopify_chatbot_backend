from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: Optional[str] = None


ALLOWLISTED_WRITE_ACTIONS = {
    "update_product_price",
    "update_inventory",
    "add_order_tag",
    "generic_graphql_mutation",
}


def check_write_policy(action_type: str, payload: dict) -> PolicyDecision:
    if action_type not in ALLOWLISTED_WRITE_ACTIONS:
        return PolicyDecision(allowed=False, reason="Action not allowlisted")
    if not isinstance(payload, dict):
        return PolicyDecision(allowed=False, reason="Invalid payload")
    return PolicyDecision(allowed=True)

