from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Optional

from dotenv import load_dotenv
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Always load backend/.env even if uvicorn is started from the repo root.
_BACKEND_DIR = Path(__file__).resolve().parents[1]
# Ensure OPENAI_API_KEY etc. are in os.environ (shell env can be empty and override otherwise).
load_dotenv(_BACKEND_DIR / ".env", override=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["dev", "prod", "test"] = "dev"

    database_url: str = "sqlite:///./dev.db"

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, v: Any) -> Any:
        if isinstance(v, str) and v.startswith("mysql://"):
            return "mysql+pymysql://" + v[len("mysql://") :]
        return v
    encryption_key_base64: str = ""

    # Shopify app config
    shopify_app_client_id: str = ""
    shopify_app_client_secret: str = ""
    shopify_app_redirect_uri: str = ""
    shopify_app_scopes: str = "read_products,read_orders,read_customers,read_inventory,write_products,write_inventory,write_orders,write_customers"
    shopify_admin_api_version: str = "2025-01"
    shopify_storefront_api_version: str = "2025-01"

    # Shopify Dev MCP (https://shopify.dev/docs/apps/build/devmcp) — subprocess via npx; used by the chat agent for API/docs discovery.
    shopify_dev_mcp_enabled: bool = True
    shopify_dev_mcp_command: str = "npx"
    # Space-separated args after command (matches Cursor: npx -y @shopify/dev-mcp@latest)
    shopify_dev_mcp_args: str = "-y @shopify/dev-mcp@latest"
    shopify_dev_mcp_opt_out_instrumentation: bool = False
    shopify_dev_mcp_liquid_validation_mode: str = ""  # e.g. full | partial; empty = server default
    # Plaintext OAuth tokens (Admin API); default file is under backend/ (gitignored).
    shopify_tokens_file: str = "data/shopify_tokens.json"

    # LLM provider(s)
    openai_api_key: Optional[str] = None

    # CORS — comma-separated origins; set to "*" to allow all (credentials will be disabled).
    cors_origins: str = "http://localhost:8080,http://localhost:3000,http://127.0.0.1:8080,http://127.0.0.1:3000"


def get_settings() -> Settings:
    """Fresh Settings each call so .env changes apply without stale process cache."""
    return Settings()

