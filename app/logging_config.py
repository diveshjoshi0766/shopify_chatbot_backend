"""Log levels for integration visibility (chat, Shopify Admin API, agent tools)."""

from __future__ import annotations

import logging
import sys


def setup_integration_logging() -> None:
    """Tune app and HTTP client loggers so integration lines are visible under uvicorn."""
    app_logger = logging.getLogger("app")
    app_logger.setLevel(logging.INFO)
    if not app_logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(name)s %(message)s"))
        app_logger.addHandler(handler)
    for name in ("app.api", "app.lang", "app.shopify"):
        logging.getLogger(name).setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
