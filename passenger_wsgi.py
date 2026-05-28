"""WSGI entry point for Plesk/Passenger deployments."""

from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

from src.web.application import app as application  # noqa: E402

