"""A fresh, synthetic runtime; configure BEFORE importing application modules."""

import os
from pathlib import Path
import secrets
import socket
import tempfile


def configure():
    root = Path(tempfile.gettempdir()) / ("fst-" + secrets.token_hex(4))
    root.mkdir()  # New directory only; never reuse an operational queue.
    for key in list(os.environ):
        if key.startswith(("FS_", "GRAPH_", "MICROSOFT_AUTH_")):
            os.environ.pop(key, None)
    os.environ.update({
        "FS_ENV_FILE": str(root / "absent.env"), "FS_ENVIRONMENT": "test",
        "FS_BASE_PATH": str(root), "FS_APP_DATA_DIR": str(root / "data"),
        "FS_EXCEL_ROOT": str(root / "excel"), "GRAPH_CACHE_DIR": str(root / "cache"),
        "FS_STORAGE_BACKEND": "local", "FS_AUTH_PROVIDER": "none",
        "FS_SECRET_KEY": secrets.token_hex(32), "FS_MAIL_ENABLED": "false",
        "FS_TEAMS_NOTIFICATIONS_ENABLED": "false", "FS_GRAPH_QUEUE_IN_WEB": "false",
        "FS_NO_BROWSER": "1", "FS_LOG_ENABLED": "false", "FS_LOG_REQUESTS": "false",
        "FS_TEST_SYNTHETIC": "true", "PYTHONDONTWRITEBYTECODE": "1",
    })
    return root


def deny_network(*_args, **_kwargs):
    raise RuntimeError("Rede externa bloqueada na versão de teste; usar transporte simulado.")


def block_outbound_network():
    # Listening/accept remain available for the loopback-only Flask server.
    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    socket.socket.sendto = deny_network
    socket.create_connection = deny_network


def seed_synthetic_data(count=3):
    from openpyxl import Workbook
    from src.config import EXCEL_ACTIVAS_DIR, ensure_directories
    from src.field_map import FIELD_MAP
    from src.services.file_service import FileService

    ensure_directories()
    sources = []
    for index in range(count):
        number = f"2026_{9901 + index}"
        book = Workbook()
        sheet = book.active
        sheet.title = "LINK"
        book.create_sheet("FS")
        for field in FIELD_MAP:
            sheet[f"{field.column}2"] = field.label
            if field.label == "Folha nº":
                sheet[f"{field.column}3"] = number
            elif field.label == "Cliente nome":
                sheet[f"{field.column}3"] = f"Cliente fictício {index + 1}"
        path = EXCEL_ACTIVAS_DIR / (number + ".xlsx")
        book.save(path)
        book.close()
        sources.append(path)
    draft = FileService(EXCEL_ACTIVAS_DIR).create_draft_copy(sources[0], "Técnico de teste")
    return sources, draft
