"""Last complete SharePoint inventory, shared by all web processes."""

import json
import math
from pathlib import Path


ACTIVE_INDEX_NAME = ".graph_active_files.json"


def read_active_inventory(directory: Path) -> dict:
    """Validate one atomic snapshot; missing/invalid never authorizes cached files."""
    try:
        payload = json.loads((directory / ACTIVE_INDEX_NAME).read_text(encoding="utf-8"))
        names = payload["files"]
        if payload.get("version") != 1 or not isinstance(names, list):
            raise ValueError("invalid inventory")
        if not all(_valid_relative_name(name) for name in names):
            raise ValueError("invalid inventory path")
        if len(set(names)) != len(names):
            raise ValueError("duplicate inventory path")
        unavailable = payload.get("unavailable_files", [])
        if (not isinstance(unavailable, list)
                or not all(isinstance(name, str) and name in names for name in unavailable)):
            raise ValueError("invalid content availability")
        updated_at = payload.get("updated_at")
        if updated_at is not None and (
            isinstance(updated_at, bool) or not isinstance(updated_at, (int, float))
            or not math.isfinite(updated_at) or updated_at <= 0
        ):
            raise ValueError("invalid inventory timestamp")
        refresh_id = payload.get("refresh_id")
        if refresh_id is not None and (not isinstance(refresh_id, str) or not refresh_id):
            raise ValueError("invalid inventory identifier")
        return {"available": True, "files": set(names), "unavailable_files": set(unavailable), "updated_at": updated_at,
                "refresh_id": refresh_id, "error": None}
    except FileNotFoundError:
        return {"available": False, "files": set(), "unavailable_files": set(), "updated_at": None,
                "refresh_id": None, "error": "missing"}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        # An unreadable index must not bring unconfirmed cache entries back.
        return {"available": False, "files": set(), "unavailable_files": set(), "updated_at": None,
                "refresh_id": None, "error": "invalid"}


def _valid_relative_name(name: object) -> bool:
    if not isinstance(name, str) or "\\" in name or ":" in name:
        return False
    parts = name.split("/")
    return len(parts) in {1, 2} and all(part not in {"", ".", ".."} for part in parts)


def read_active_index(directory: Path, *, required: bool = False) -> set[str] | None:
    inventory = read_active_inventory(directory)
    if inventory["error"] == "missing" and not required:
        return None
    return inventory["files"] - inventory["unavailable_files"]
