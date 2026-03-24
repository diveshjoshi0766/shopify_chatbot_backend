## Shopify multi-store LangChain chatbot (backend)

This is a FastAPI backend that hosts a LangGraph-based agent and integrates with Shopify Admin + Storefront APIs across multiple stores (multi-tenant).

### Shopify Dev MCP (IDE + runtime)

[Shopify Dev MCP](https://shopify.dev/docs/apps/build/devmcp) is available in two ways:

1. **Cursor / IDE:** configured in **`.cursor/mcp.json`** for assistant-driven docs and validation while you edit code.
2. **Chat agent (this API):** each `POST /chat` can spawn `npx @shopify/dev-mcp@latest` over stdio (Python `mcp` SDK) so the agent can call `learn_shopify_api`, `introspect_graphql_schema`, `search_docs_chunks`, and `validate_graphql_codeblocks` before executing **`shopify_admin_graphql`** against the merchant. Requires **Node.js 18+** (`npx` on `PATH`) and **`mcp`** in `requirements.txt`. Tune with `SHOPIFY_DEV_MCP_*` in `backend/.env`. See **`docs/shopify-dev-mcp.md`**.

### Local setup

- Create and activate a virtualenv
- Install dependencies

```bash
cd backend
pip install -r requirements.txt
```

### Configure environment

Create a `.env` in `backend/` (do not commit it) with:

```bash
APP_ENV=dev
# SQLite (default if unset): sqlite:///./dev.db
# MySQL: mysql://user:pass@host:3306/dbname  (auto-converted to mysql+pymysql://)
# Postgres: postgresql+psycopg://postgres:postgres@localhost:5432/shopify_chatbot
DATABASE_URL=mysql://user:password@host:3306/your_database
ENCRYPTION_KEY_BASE64=...

SHOPIFY_APP_CLIENT_ID=...
SHOPIFY_APP_CLIENT_SECRET=...
SHOPIFY_APP_REDIRECT_URI=http://localhost:8000/shopify/callback
SHOPIFY_APP_SCOPES=read_products,read_orders,read_customers,read_inventory,write_products,write_inventory,write_orders,write_customers

OPENAI_API_KEY=...

# Optional: path for OAuth tokens (default: data/shopify_tokens.json under backend/)
# SHOPIFY_TOKENS_FILE=data/shopify_tokens.json
```

OAuth and manual token imports write **plaintext Admin tokens** to `data/shopify_tokens.json` (gitignored). `ENCRYPTION_KEY_BASE64` is only needed for legacy rows that still use encrypted DB tokens.

Generate an encryption key (legacy / optional):

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### Run

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### Logs and health checks

- **Structured logs** (stdout): `chat_start` / `chat_end`, `agent_done` (tool names + latency), per-tool lines in `app.shopify.tools`, and Shopify HTTP/GraphQL outcomes in `app.shopify.admin_client`. Use `LOG_LEVEL=debug` on uvicorn only if you need more noise.
- **`GET /health`**: returns `integrations` including **`shopify_dev_mcp`** (runtime settings, whether the Python MCP SDK imported, and the configured `npx` args).

### Key endpoints

- `POST /chat`: chat with the agent; write actions return a pending action requiring confirmation.
- `POST /chat/confirm`: confirm and execute a pending write action.
- `GET /shopify/install`: start OAuth for a store.
- `GET /shopify/callback`: OAuth callback.
- `POST /admin/stores/manual-token`: (internal) import an existing Admin token for a store.

