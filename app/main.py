"""
FastAPI application factory and lifespan for the Shopify multi-store LangChain chatbot.

Manages CORS, MongoDB init/indexes, singleton Shopify Dev MCP session, and
in-process conversation memory (LangGraph MemorySaver).
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langgraph.checkpoint.memory import MemorySaver
from pymongo.errors import PyMongoError, ServerSelectionTimeoutError

from app.api.routes_admin import router as admin_router
from app.api.routes_auth import router as auth_router
from app.api.routes_chat import router as chat_router
from app.api.routes_integrations import router as integrations_router
from app.api.routes_shopify import router as shopify_router
from app.bootstrap_admin import ensure_bootstrap_admin_user
from app.db import ensure_mongo_schema, get_tool_repository
from app.logging_config import setup_integration_logging
from app.settings import get_settings
from app.shopify.mcp_dev import (
    env_block_for_shopify_mcp,
    mcp_import_error,
    mcp_sdk_available,
    parse_mcp_args,
    try_start_shopify_dev_mcp,
)
from app.shopify.token_store import token_file_path

_log = logging.getLogger("uvicorn.error")
_app_log = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_integration_logging()
    _app_log.info(
        "Integration logging enabled (app.* = INFO). "
        "Shopify Dev MCP can also run inside this API (stdio via npx); see backend settings / health."
    )

    try:
        await asyncio.to_thread(ensure_mongo_schema)
        _log.info("MongoDB indexes ready.")
        await asyncio.to_thread(ensure_bootstrap_admin_user)
    except Exception as e:  # noqa: BLE001
        _log.error("MongoDB init failed — fix MONGODB_URI / network / Atlas access. Error: %s", e)

    settings = get_settings()
    app.state.mcp_session = None
    app.state.memory = MemorySaver()
    _app_log.info("LangGraph MemorySaver initialized (in-process conversation memory).")

    if getattr(settings, "shopify_dev_mcp_enabled", True):

        async def _start_mcp_background() -> None:
            _app_log.info("Starting singleton Shopify Dev MCP session (npx @shopify/dev-mcp) in background...")
            try:
                sess = await asyncio.to_thread(
                    lambda: try_start_shopify_dev_mcp(
                        command=settings.shopify_dev_mcp_command,
                        args=parse_mcp_args(settings.shopify_dev_mcp_args),
                        extra_env=env_block_for_shopify_mcp(settings),
                    )
                )
                app.state.mcp_session = sess
                if sess:
                    _app_log.info("Shopify Dev MCP session ready (singleton, shared across chat requests).")
                else:
                    _app_log.warning("Shopify Dev MCP session could not start — agent will run without MCP tools.")
            except Exception:  # noqa: BLE001
                _app_log.exception("Shopify Dev MCP session failed during startup.")

        app.state._mcp_startup_task = asyncio.create_task(_start_mcp_background())
    else:
        _app_log.info("Shopify Dev MCP disabled (SHOPIFY_DEV_MCP_ENABLED=false).")

    yield

    mcp_task = getattr(app.state, "_mcp_startup_task", None)
    if mcp_task is not None and not mcp_task.done():
        mcp_task.cancel()
        try:
            await mcp_task
        except asyncio.CancelledError:
            pass

    mcp_session = getattr(app.state, "mcp_session", None)
    if mcp_session is not None:
        _app_log.info("Shutting down singleton Shopify Dev MCP session...")
        mcp_session.close()
    _app_log.info("Lifespan shutdown complete.")


def create_app() -> FastAPI:
    app = FastAPI(title="Shopify multi-store LangChain chatbot", lifespan=lifespan)

    @app.exception_handler(PyMongoError)
    async def mongo_error_handler(_request: Request, exc: PyMongoError):
        msg = str(exc)
        if isinstance(exc, ServerSelectionTimeoutError) or "ServerSelectionTimeoutError" in type(exc).__name__:
            detail = (
                "Cannot reach MongoDB (timeout). Check MONGODB_URI, Atlas IP allowlist, and network. "
                "See backend/.env MONGODB_URI."
            )
        elif "authentication failed" in msg.lower() or "bad auth" in msg.lower():
            detail = "MongoDB authentication failed — check username/password in MONGODB_URI."
        else:
            detail = "MongoDB error. Verify MONGODB_URI and that the cluster is reachable."
        return JSONResponse(status_code=503, content={"detail": detail, "error": msg})

    settings = get_settings()
    raw_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    is_wildcard = raw_origins == ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=raw_origins,
        allow_credentials=not is_wildcard,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health():
        settings = get_settings()
        token_path = token_file_path()
        openai_ok = bool((settings.openai_api_key or "").strip())
        shopify_app_ok = bool(
            (settings.shopify_app_client_id or "").strip()
            and (settings.shopify_app_client_secret or "").strip()
        )
        db_ok = False
        db_detail: str | None = None
        try:
            get_tool_repository().ping()
            db_ok = True
        except Exception as e:  # noqa: BLE001
            db_detail = str(e)

        mcp_session = getattr(app.state, "mcp_session", None)
        integrations = {
            "database": "connected" if db_ok else "error",
            "mongodb_collection": settings.mongodb_collection,
            "openai_api_key_configured": openai_ok,
            "shopify_oauth_app_configured": shopify_app_ok,
            "shopify_admin_api_version": settings.shopify_admin_api_version,
            "shopify_tokens_file_path": str(token_path),
            "shopify_tokens_file_present": token_path.is_file(),
            "ready_for_chat": db_ok and openai_ok,
            "shopify_dev_mcp": {
                "runtime_enabled_setting": getattr(settings, "shopify_dev_mcp_enabled", True),
                "singleton_alive": mcp_session.is_alive() if mcp_session else False,
                "python_mcp_sdk_available": mcp_sdk_available(),
                "python_mcp_sdk_error": mcp_import_error(),
                "command": getattr(settings, "shopify_dev_mcp_command", "npx"),
                "args": parse_mcp_args(getattr(settings, "shopify_dev_mcp_args", "")),
                "note": "Singleton MCP session shared across all chat requests (requires Node 18+). "
                "IDE still uses .cursor/mcp.json separately.",
            },
        }
        if db_detail:
            integrations["database_error"] = db_detail
        return {
            "ok": db_ok,
            "integrations": integrations,
        }

    app.include_router(shopify_router)
    app.include_router(integrations_router)
    app.include_router(admin_router)
    app.include_router(chat_router)
    app.include_router(auth_router)

    return app


app = create_app()
