"""Allowlisted diagnostics shared by technician polling and administration."""
import math
import re
from urllib.error import HTTPError, URLError


OAUTH_ERRORS = frozenset({"access_denied", "server_error", "temporarily_unavailable", "invalid_request",
    "unauthorized_client", "unsupported_response_type", "invalid_scope", "interaction_required",
    "login_required", "consent_required"})


def known_provider_cause(detail):
    """Classify known diagnostics without ever returning provider text."""
    text = str(detail).casefold()
    if "mail.send" in text:
        return "missing_mail_send"
    if any(marker in text for marker in ("aadsts7000215", "invalid_client", "id do secret")):
        return "invalid_client_secret"
    if "token de acesso inválido" in text:
        return "invalid_access_token"
    if "configuração" in text and "incompleta" in text:
        return "configuration_missing"
    return None


def safe_provider_failure(error):
    """Keep a bounded classification and numeric status from explicit causes.

    Exceptions retain their private cause internally. Do not serialize a cause,
    URL, body or arbitrary OAuth string just to make a public reference useful.
    """
    cause = ("identity_invalid" if str(getattr(error, "code", "")).startswith("identity_")
             else known_provider_cause(error))
    result = {"error_type": type(error).__name__}
    current = error
    for _ in range(8):
        if current is None:
            break
        diagnostic_cause = getattr(current, "diagnostic_cause", None)
        if diagnostic_cause in {"invalid_client_secret", "missing_mail_send", "invalid_access_token", "configuration_missing"}:
            cause = cause or diagnostic_cause
        if isinstance(current, HTTPError) and type(current.code) is int and 100 <= current.code <= 599:
            result.setdefault("http_status", current.code)
        elif isinstance(current, (URLError, TimeoutError, ConnectionError)):
            cause = cause or "unavailable"
        current = current.__cause__
    status = result.get("http_status")
    result["cause"] = cause or ({401: "token_rejected", 403: "access_denied", 429: "rate_limited"}.get(status)) or (
        "unavailable" if status is not None and status >= 500 else
        "configuration_invalid" if type(error).__name__ == "GraphMailConfigurationError" else "provider_rejected")
    return result


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
