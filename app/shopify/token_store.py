from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Optional

from app.models import StoreConnection
from app.settings import get_settings

_lock = Lock()


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _token_file_path() -> Path:
    raw = get_settings().shopify_tokens_file
    p = Path(raw)
    if p.is_absolute():
        return p
    return _backend_root() / p


def token_file_path() -> Path:
    """Resolved path to the JSON token file (for health checks and tooling)."""
    return _token_file_path()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_raw() -> dict[str, Any]:
    path = _token_file_path()
    if not path.is_file():
        return {"version": 1, "stores": []}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return {"version": 1, "stores": []}
    stores = data.get("stores")
    if not isinstance(stores, list):
        data["stores"] = []
    data.setdefault("version", 1)
    return data


def _write_raw(data: dict[str, Any]) -> None:
    path = _token_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def upsert_store_token(
    *,
    store_id: str,
    tenant_id: str,
    shop_domain: str,
    access_token: str,
    scopes: list[str],
) -> None:
    """Persist OAuth / manual Admin API token to the JSON file (primary store for local dev)."""
    with _lock:
        data = _read_raw()
        stores: list[dict[str, Any]] = data["stores"]
        row = {
            "store_id": store_id,
            "tenant_id": tenant_id,
            "shop_domain": shop_domain,
            "access_token": access_token,
            "scopes": scopes,
            "updated_at": _now_iso(),
        }
        replaced = False
        for i, s in enumerate(stores):
            if s.get("store_id") == store_id:
                stores[i] = row
                replaced = True
                break
        if not replaced:
            stores.append(row)
        _write_raw(data)


def get_json_token(store_id: str) -> Optional[str]:
    """Return plaintext Admin token from JSON if present."""
    data = _read_raw()
    for s in data.get("stores", []):
        if s.get("store_id") == store_id:
            tok = s.get("access_token")
            if isinstance(tok, str) and tok.strip():
                return tok
    return None


def get_access_token_for_store(store: StoreConnection) -> str:
    """
    Resolve Admin API access token: JSON file first, then legacy Fernet-encrypted DB column.
    """
    from app.crypto import decrypt_str

    t = get_json_token(store.id)
    if t:
        return t
    if store.access_token_enc and store.access_token_enc.strip():
        return decrypt_str(store.access_token_enc)
    raise RuntimeError(
        f"No access token for store {store.id} ({store.shop_domain}). "
        "Complete OAuth or import a manual token."
    )
