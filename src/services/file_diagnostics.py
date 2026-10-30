"""Privacy-preserving diagnostics; never log file names, URLs or document data."""

import contextvars
import hashlib
import logging
import re
from pathlib import Path

from src.logging_config import log_event


SYNC_ID = contextvars.ContextVar("file_diagnostic_sync_id", default=None)
LOGGER = logging.getLogger(__name__)


def fingerprint(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:24]


def file_fields(name):
    name = Path(str(name)).name
    match = re.match(r"^(\d{4}_\d{4})(?:[ _ .]|$)", name)
    # Strip only known Excel extensions; dots may be part of a client's name.
    stem = name[:-5] if name.lower().endswith((".xlsx", ".xlsm")) else name
    return {
        "sheet_number": match.group(1) if match else None,
        "file_ref": fingerprint(stem),
        "extension": Path(name).suffix.lower() if name.lower().endswith((".xlsx", ".xlsm")) else None,
    }


def diagnostic(event, *, level=logging.INFO, **fields):
    log_event(LOGGER, level, "Diagnóstico de disponibilidade de folhas.",
              event=event, sync_id=SYNC_ID.get(), **fields)


def lookup_fields(service, name):
    """Best-effort, read-only snapshot; a concurrent mutation must not break a request."""
    fields = file_fields(name)
    try:
        if not name or name != name.strip() or any(c in name for c in '/\\:\x00'):
            return {**fields, "lookup_reason": "invalid_name"}
        target = service.directory / f"{name}.xlsx"
        if not service._contained(target):
            return {**fields, "lookup_reason": "invalid_path"}
        bundle = service.directory / name
        fields.update(
            expected_exists=target.is_file(),
            alternate_xlsm_exists=(service.directory / f"{name}.xlsm").is_file(),
            bundle_exists=bundle.is_dir(),
            archived_marker=service._is_archived_single_file(target) or service._is_archived_bundle_dir(bundle),
        )
        fields["lookup_reason"] = (
            "archived_marker" if fields["archived_marker"] else
            "alternate_extension" if fields["alternate_xlsm_exists"] else
            "not_resolved" if fields["expected_exists"] else "local_file_absent"
        )
        from src.services.active_file_index import read_active_index
        inventory = read_active_index(service.directory)
        fields["last_inventory_available"] = inventory is not None
        fields["expected_in_last_inventory"] = f"{name}.xlsx" in inventory if inventory is not None else None
        # On failure only, reveal bounded, anonymized candidates with the same sheet number.
        if fields["sheet_number"]:
            candidates = []
            for item in service.directory.iterdir():
                if item.suffix.lower() not in {".xlsx", ".xlsm"}:
                    continue
                candidate = file_fields(item.name)
                if candidate["sheet_number"] == fields["sheet_number"]:
                    candidates.append(candidate)
                    if len(candidates) == 20:
                        break
            fields["local_candidates"] = candidates
    except (OSError, ValueError, RuntimeError) as exc:
        fields.update(lookup_reason="diagnostic_unavailable", diagnostic_error_type=type(exc).__name__)
    return fields
