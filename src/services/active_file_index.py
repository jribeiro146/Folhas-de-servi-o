"""Last complete SharePoint inventory, shared by all web processes."""

import json
from pathlib import Path


ACTIVE_INDEX_NAME = ".graph_active_files.json"


def read_active_index(directory: Path, *, required: bool = False) -> set[str] | None:
    try:
        payload = json.loads((directory / ACTIVE_INDEX_NAME).read_text(encoding="utf-8"))
        names = payload["files"]
        if payload.get("version") != 1 or not isinstance(names, list):
            return set()
        if not all(isinstance(name, str) for name in names):
            return set()
        return set(names)
    except FileNotFoundError:
        return set() if required else None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        # An unreadable index must not bring unconfirmed cache entries back.
        return set()
