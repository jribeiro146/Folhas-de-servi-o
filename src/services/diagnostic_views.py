"""Allowlisted diagnostics shared by technician polling and administration."""
import math
import re


REFRESH_ERROR = "Não foi possível concluir a atualização. A lista pode estar desatualizada ou ter conteúdo em falta."
CONFIG_FIELDS = frozenset({"GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET", "GRAPH_DRIVE_ID",
                          "FS_MAIL_SENDER", "FS_TEAMS_WEBHOOK_URL"})


def _number(value, *, integer=False):
    if type(value) not in ((int,) if integer else (int, float)):
        return None
    try:
        return value if value >= 0 and math.isfinite(value) else None
    except OverflowError:
        return None


def _identifier(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) else None


def safe_refresh_status(value):
    if not isinstance(value, dict):
        return None
    result = {"in_progress": value.get("in_progress") is True,
              "last_error": REFRESH_ERROR if value.get("last_error") else None,
              "error_code": "graph_refresh_failed" if value.get("last_error") else None}
    for key in ("outcome", "last_completed_outcome"):
        outcome = value.get(key)
        result[key] = outcome if outcome in ("idle", "running", "success", "error") else None
    for key in ("refresh_id", "generation_id", "last_completed_refresh_id"):
        result[key] = _identifier(value.get(key))
    for key in ("refresh_sequence", "last_completed_sequence"):
        result[key] = _number(value.get(key), integer=True)
    for key in ("last_started_at", "last_completed_at", "last_success_at"):
        result[key] = _number(value.get(key))
    inventory = value.get("inventory")
    if isinstance(inventory, dict):
        result["inventory"] = {"available": inventory.get("available") is True,
            "stale": inventory.get("stale") is not False,
            "updated_at": _number(inventory.get("updated_at")),
            "refresh_id": _identifier(inventory.get("refresh_id")),
            "file_count": _number(inventory.get("file_count"), integer=True),
            "unavailable_count": _number(inventory.get("unavailable_count"), integer=True),
            "error": REFRESH_ERROR if inventory.get("error") else None}
    return result


def safe_service_status(value):
    value = value if isinstance(value, dict) else {}
    result = {key: value[key] for key in ("enabled", "configured", "drive_id", "test_mode",
              "authenticated", "mail_send_permission") if type(value.get(key)) is bool}
    if isinstance(value.get("missing_fields"), list):
        result["missing_fields"] = [field for field in value["missing_fields"]
                                    if isinstance(field, str) and field in CONFIG_FIELDS]
    return result


def safe_queue_summary(value):
    if not isinstance(value, dict):
        return None
    return {key: _number(value.get(key), integer=True) or 0 for key in
            ("pending", "held", "running", "failed", "complete", "teams_missing", "teams_conflicts")}
