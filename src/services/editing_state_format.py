"""Decode persisted editing evidence without treating corruption as new state."""
import json
import math
import hashlib
import logging

STATE_VERSION = 2


class EditingStateError(Exception):
    """Base error for editing-state operations."""


class EditingStateQuarantinedError(EditingStateError):
    """Existing evidence must be preserved until explicit recovery."""

    def __init__(self):
        super().__init__("O estado desta folha requer recuperação pelo administrador. Os dados guardados foram preservados.")


class EditingStateWriteInvalidError(EditingStateError):
    """A proposed mutation was rejected; stored evidence is still valid."""

    def __init__(self):
        super().__init__("A alteração não é válida e não foi guardada. O estado anterior da folha foi preservado.")


def report_quarantined_state(document_id):
    logging.getLogger(__name__).warning("Estado de edição em quarentena.", extra={
        "event": "editing_state_quarantined",
        "event_fields": {"document_ref": hashlib.sha256(str(document_id).encode("utf-8")).hexdigest()[:16]},
    })


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EditingStateQuarantinedError()
        result[key] = value
    return result


def decode_state_object(raw):
    try:
        state = json.loads(raw, object_pairs_hook=_unique_object)
    except (TypeError, ValueError, RecursionError) as exc:
        raise EditingStateQuarantinedError() from exc
    if not isinstance(state, dict):
        raise EditingStateQuarantinedError()
    return state


def _require(condition):
    if not condition:
        raise EditingStateQuarantinedError()


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _session(value, token):
    _require(isinstance(value, dict))
    _require(_text(token) and value.get("token") == token)
    _require(_text(value.get("owner_id")) and _text(value.get("client_id")))
    _require(isinstance(value.get("owner_name"), str))
    for field in ("acquired_at", "last_seen_at", "expires_at"):
        _require(_number(value.get(field)))


def validate_editing_state(state, document_id):
    """Validate the current aggregate before normalizing or committing it.

    Unknown fields are preserved. Older/future versions require explicit migration;
    this function never repairs, guesses ownership or drops corrupt evidence.
    """
    _require(isinstance(state, dict))
    _require(type(state.get("version")) is int and state["version"] == STATE_VERSION)
    _require(_text(document_id) and state.get("document_id") == document_id)
    _require(type(state.get("revision")) is int and state["revision"] >= 1)
    for field in ("etag", "fingerprint"):
        _require(isinstance(state.get(field), str))
    _require("source_changed_at" in state and (state["source_changed_at"] is None or _number(state["source_changed_at"])))
    _require(isinstance(state.get("aliases"), list) and all(isinstance(x, str) for x in state["aliases"]))
    _require(state.get("workspace_kind") in ("draft", "private"))
    for field in ("owner", "lease", "autosave"):
        _require(field in state and (state[field] is None or isinstance(state[field], dict)))
    owner = state["owner"]
    if owner is not None:
        _require(_text(owner.get("owner_id")) and isinstance(owner.get("owner_name"), str))
        _require(_number(owner.get("assigned_at")))
    _require(isinstance(state.get("sessions"), dict))
    for token, session in state["sessions"].items():
        _session(session, token)
    if state["lease"] is not None:
        _session(state["lease"], state["lease"].get("token"))
    autosave = state["autosave"]
    if autosave is not None:
        _require(isinstance(autosave.get("document"), dict) and _number(autosave.get("updated_at")))
        _require(_text(autosave.get("owner_id")) and _text(autosave.get("idempotency_key")))
        _require(isinstance(autosave.get("owner_name"), str))
    _require(isinstance(state.get("operations"), dict))
    for key, operation in state["operations"].items():
        _require(_text(key) and isinstance(operation, dict))
        _require(operation.get("status") in ("pending", "failed", "complete"))
        _require(_text(operation.get("kind")) and _text(operation.get("owner_id")))
        _require(type(operation.get("base_revision")) is int and operation["base_revision"] >= 1)
        for field in ("created_at", "updated_at"):
            _require(_number(operation.get(field)))
        _require(isinstance(operation.get("context"), dict))
        context = operation["context"]
        if "source_path" in context:
            _require(_text(context["source_path"]))
        if "source_retired" in context:
            _require(type(context["source_retired"]) is bool)
        if operation["status"] == "complete":
            _require(isinstance(operation.get("response"), dict))
    blocked = state.get("admin_blocked_users", {})
    _require(isinstance(blocked, dict))
    for key, value in blocked.items():
        _require(_text(key) and isinstance(value, dict))
        _require(value.get("name") is None or isinstance(value["name"], str))
        _require(_number(value.get("at")))
    for field in ("original_name", "closed_by_operation"):
        if field in state:
            _require(isinstance(state[field], str))
    return state


def decode_editing_state(raw, document_id):
    return validate_editing_state(decode_state_object(raw), document_id)
