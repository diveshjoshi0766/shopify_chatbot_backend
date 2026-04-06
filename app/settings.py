from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Optional

from dotenv import load_dotenv
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Always load backend/.env even if uvicorn is started from the repo root.
_BACKEND_DIR = Path(__file__).resolve().parents[1]
load_dotenv(_BACKEND_DIR / ".env", override=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["dev", "prod", "test"] = "dev"

    # MongoDB Atlas or local — database name should be in the URI path unless mongodb_database is set.
    mongodb_uri: str = "mongodb://127.0.0.1:27017/dyspensr_ai_bot"
    """Connection URI including database path, e.g. mongodb+srv://user:pass@host/Calidevelopment?retryWrites=true&w=majority"""

    mongodb_database: str = ""
    """If set, overrides the database name parsed from mongodb_uri."""

    mongodb_collection: str = "dyspensr_ai_bot"
    """Single collection storing all entity types (discriminated by `entity` field)."""

    @field_validator("mongodb_uri", mode="before")
    @classmethod
    def strip_mongo_uri(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip()
        return v

    encryption_key_base64: str = ""

    # Shopify app config
    shopify_app_client_id: str = ""
    shopify_app_client_secret: str = ""
    shopify_app_redirect_uri: str = ""
    shopify_app_scopes: str = "read_products,read_orders,read_customers,read_inventory,write_products,write_inventory,write_orders,write_customers"
    shopify_admin_api_version: str = "2025-01"
    shopify_storefront_api_version: str = "2025-01"

    shopify_dev_mcp_enabled: bool = True
    shopify_dev_mcp_command: str = "npx"
    shopify_dev_mcp_args: str = "-y @shopify/dev-mcp@latest"
    shopify_dev_mcp_opt_out_instrumentation: bool = False
    shopify_dev_mcp_liquid_validation_mode: str = ""
    shopify_tokens_file: str = "data/shopify_tokens.json"

    openai_api_key: Optional[str] = None

    cors_origins: str = "http://localhost:8080,http://localhost:3000,http://127.0.0.1:8080,http://127.0.0.1:3000"

    auth_token_secret: str = "dev-insecure-secret-change-me"
    auth_token_ttl_seconds: int = 60 * 60 * 8
    auth_allow_legacy_headers: bool = True
    auth_registration_password: str = ""
    auth_admin_register_email: str = ""
    auth_admin_register_password: str = ""
    auth_admin_email: str = "diveshjoshi0766@gmail.com"
    default_tenant_id: str = "t1"

    def resolved_mongo_database_name(self) -> str:
        if (self.mongodb_database or "").strip():
            return self.mongodb_database.strip()
        from pymongo.uri_parser import parse_uri

        try:
            info = parse_uri(self.mongodb_uri)
            db = (info.get("database") or "").strip()
            if db:
                return db
        except Exception:  # noqa: BLE001
            pass
        return "admin"


def get_settings() -> Settings:
    return Settings()
