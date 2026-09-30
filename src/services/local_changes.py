"""Protect local bundle versions until their exact publication is confirmed."""

import hashlib
import os
import secrets
from pathlib import Path

from src.services.file_mutex import file_mutex

DIRTY_MARKER = ".fs-local-dirty"


def bundle_guard(directory: Path):
    identifier = hashlib.sha256(os.path.normcase(str(directory.resolve())).encode()).hexdigest()
    return file_mutex(directory.parent / ".fs-locks" / identifier)


def mark_dirty(directory: Path) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(16)
    temporary = directory / (DIRTY_MARKER + ".tmp")
    temporary.write_text(token, encoding="ascii")
    os.replace(temporary, directory / DIRTY_MARKER)
    # An imported legacy marker must no longer be mistaken for this local edit.
    (directory / (DIRTY_MARKER + ".graph.json")).unlink(missing_ok=True)
    return token


def dirty_version(directory: Path) -> str:
    try:
        return (directory / DIRTY_MARKER).read_text(encoding="ascii")
    except FileNotFoundError:
        return ""
