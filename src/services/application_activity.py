"""Read-only, bounded activity view over existing operation receipts.

No second audit database or schema migration: a durable claim is the event and
its idempotency key is its identity. Queue receipts supply the external outcome.
This is recent activity, not an unlimited audit trail: normal receipt retention
and the editing aggregate's 64-operation limit still apply.
"""
import heapq
import json
import math
import unicodedata

from src.logging_config import redact_diagnostic_text
from src.services.editing_state_format import decode_editing_state, EditingStateQuarantinedError
from src.services.graph_sync_queue import delivery_uncertain


KINDS = frozenset({"send", "draft", "cancel"})
LIMIT = 100


def _label(value, fallback):
    if not isinstance(value, str) or not value.strip():
        return fallback
    return redact_diagnostic_text(value.strip())[:200]


def _time(value):
    try:
        return value if type(value) in (int, float) and math.isfinite(value) and 0 <= value < 253402300800 else None
    except OverflowError:
        return None


def _actor(value):
    # Microsoft legitimately falls back to the account email when displayName
    # is absent. This admin-only attribution is not a diagnostic to redact.
    if not isinstance(value, str):
        return "Autor não registado"
    return "".join(c for c in value if not unicodedata.category(c).startswith("C")).strip()[:200] or "Autor não registado"


def _object(raw):
    value = json.loads(raw) if raw is not None else {}
    if not isinstance(value, dict):
        raise ValueError("Invalid activity source")
    return value


def _external(row):
    """Do not equate local commit, queue completion or timestamps with delivery."""
    try:
        payload, result = _object(row["payload_json"]), _object(row["result_json"])
        raw_mail = result.get("mail")
        # archive_and_remove explicitly records null when no email was asked
        # for. Other non-object values remain invalid evidence.
        if raw_mail is not None and not isinstance(raw_mail, dict):
            raise ValueError()
        mail = raw_mail or {}
        if "accepted" in mail and type(mail["accepted"]) is not bool:
            raise ValueError()
        has_mail = row["kind"] == "archive_and_mail_local" or bool(payload.get("mail"))
        status = row["status"]
        if (status not in {"held", "pending", "running", "failed", "complete", "cancelled"}
                or any(result.get(flag) for flag in ("invalid_state", "migration_uncertain", "restore_uncertain", "reconciliation_hold"))):
            raise ValueError()
        if mail.get("accepted") is True:
            mail_status = "accepted"
        elif not has_mail:
            mail_status = "not_requested"
        elif result.get("phase") == "mail_rejected":
            mail_status = "rejected"
        elif (result.get("phase") in {"sending_mail", "mail_unknown"} or status == "complete"
              or (row["attempts"] > 0 and not result.get("phase"))):
            mail_status = "unknown"
        elif result.get("phase") not in {None, "", "uploading_archive", "generating_pdf", "pdf_ready",
                                       "message_ready", "mail_snapshot_invalid", "removing_active", "active_removed"}:
            mail_status = "unknown"
        else:
            mail_status = "pending"
        if row["kind"] == "upload_active" and status == "complete" and result.get("superseded_by"):
            status = "superseded"
        return {"queue_status": status, "mail_status": mail_status,
                "reconciliation_required": delivery_uncertain(row["kind"], result) or mail_status == "unknown",
                "updated_at": _time(row["updated_at"])}
    except (TypeError, ValueError, RecursionError):
        return {"queue_status": "unavailable", "mail_status": "unknown", "updated_at": None,
                "reconciliation_required": True}


def recent_activity(repository, queue=None, *, kind="all"):
    if kind != "all" and kind not in KINDS:
        raise ValueError("Tipo de atividade inválido.")
    skipped = 0

    def events():
        nonlocal skipped
        with repository._connect() as connection:
            # Stream aggregates and retain only the newest 101 small projections.
            # Invalid states stay untouched and are counted in the response.
            for row in connection.execute("SELECT document_id,state_json FROM editing_states"):
                try:
                    state = decode_editing_state(row["state_json"], row["document_id"])
                except EditingStateQuarantinedError:
                    skipped += 1
                    continue
                for key, operation in state["operations"].items():
                    op_kind = operation["kind"]
                    if op_kind not in KINDS or (kind != "all" and op_kind != kind):
                        continue
                    metadata = operation.get("activity")
                    metadata = metadata if isinstance(metadata, dict) else {}
                    response = operation.get("response") or {}
                    identifier = f"{op_kind}:{row['document_id']}:{key}"
                    yield {"id": identifier, "kind": op_kind,
                           "sheet_name": _label(metadata.get("sheet_name") or state.get("original_name")
                                                or next(iter(state["aliases"]), ""), "Ficha sem nome registado"),
                           "actor_name": _actor(metadata.get("actor_name")),
                           "created_at": _time(operation["created_at"]),
                           "updated_at": _time(operation["updated_at"]),
                           "status": operation["status"],
                           "reconciliation_required": False,
                           "queue_status": "unavailable" if response.get("graph_job_id") else "none",
                           "mail_status": "unknown" if response.get("graph_job_id") else "not_recorded"}

    entries = heapq.nlargest(LIMIT + 1, events(), key=lambda e: (e["created_at"] or 0, e["id"]))
    has_more = len(entries) > LIMIT
    entries = entries[:LIMIT]
    if queue is not None and entries:
        with queue._connect() as connection:
            placeholders = ",".join("?" for _ in entries)
            rows = connection.execute(f"SELECT id,kind,payload_json,result_json,status,attempts,updated_at "
                                      f"FROM graph_sync_jobs WHERE id IN ({placeholders})",
                                      [entry["id"] for entry in entries]).fetchall()
        jobs = {row["id"]: row for row in rows}
        for entry in entries:
            if entry["id"] in jobs:
                outcome = _external(jobs[entry["id"]])
                # This is the latest state observation, never an email sent_at.
                outcome["updated_at"] = max(entry["updated_at"] or 0, outcome["updated_at"] or 0) or None
                entry.update(outcome)
    return {"entries": entries, "limit": LIMIT, "has_more": has_more, "unavailable_documents": skipped}
