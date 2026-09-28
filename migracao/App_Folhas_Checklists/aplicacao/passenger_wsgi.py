"""WSGI entry point for Plesk/Passenger deployments."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

from src.logging_config import configure_logging, log_event  # noqa: E402


configure_logging("web")

try:
    from src.web.application import app as application  # noqa: E402
except Exception:
    log_event(
        logging.getLogger(__name__),
        logging.CRITICAL,
        "A aplicação web não arrancou.",
        event="web_application_start_failed",
        exc_info=True,
    )
    raise


log_event(
    application.logger,
    logging.INFO,
    "Aplicação web pronta para receber pedidos.",
    event="web_application_ready",
)
