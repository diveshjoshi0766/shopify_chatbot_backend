from __future__ import annotations

from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    store_id: Optional[str] = None
    store_ids: Optional[List[str]] = None
    shop_domain: Optional[str] = None


class StoreChoice(BaseModel):
    store_id: str
    shop_domain: str


class ChatResponse(BaseModel):
    type: Literal["message", "needs_store_selection", "needs_confirmation"]
    message: str
    stores: Optional[List[StoreChoice]] = None
    pending_action_id: Optional[str] = None
    pending_action_summary: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class ConfirmRequest(BaseModel):
    pending_action_id: str
    approve: bool = True

