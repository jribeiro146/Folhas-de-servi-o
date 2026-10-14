"""Owner checks and a deliberately small browser projection of queue state."""
import math
from uuid import UUID


PHASES = frozenset({
    "uploading_archive", "generating_pdf", "pdf_ready", "removal_unknown", "removing_active", "active_removed",
    "cancellation_proof_missing", "canceling_remote", "cancellation_rejected", "cancellation_confirmed",
    "message_ready", "mail_snapshot_invalid", "mail_rejected", "sending_mail", "mail_unknown", "mail_accepted",
    "teams_rejected", "sending_teams", "teams_unknown", "teams_accepted",
})
STATUSES = frozenset({"held", "pending", "running", "failed", "complete", "cancelled"})


def _guid(value):
    if not isinstance(value, str):
        return None
    try:
        normalized = str(UUID(value))
    except ValueError:
        return None
    return normalized if normalized == value.lower() else None


def normalize_job_owner(value):
    if not isinstance(value, dict):
        return None
    if value.get("provider") == "microsoft":
        tenant, oid = _guid(value.get("tenant_id")), _guid(value.get("object_id"))
        return {"provider": "microsoft", "tenant_id": tenant, "object_id": oid} if tenant and oid else None
    if value.get("provider") == "local":
        identifier = value.get("id")
        if isinstance(identifier, str) and 0 < len(identifier) <= 200:
            return {"provider": "local", "id": identifier}
    return None


def microsoft_job_owner(user):
    return normalize_job_owner({"provider": "microsoft", "tenant_id": getattr(user, "tenant_id", None),
                                "object_id": getattr(user, "id", None)})


def can_read_job(job, principal, *, administrator=False):
    if administrator:
        return True
    payload = job.get("payload")
    owner = normalize_job_owner(payload.get("_owner")) if isinstance(payload, dict) else None
    # No inference from paths, email, ID prefixes or the currently editing user.
    # Legacy/incomplete ownership stays available only to the administrator.
    return owner is not None and owner == normalize_job_owner(principal)


def _timestamp(value):
    if type(value) not in (int, float):
        return None
    try:
        value = float(value)
    except OverflowError:
        return None
    return value if math.isfinite(value) and value >= 0 else None


def public_job_status(job):
    """Keep internal serialization for workers; allowlist every browser field.

    Never copy result/payload wholesale: they contain paths, receipts, raw
    provider errors and mail envelopes. Even phase strings must be enumerated.
    """
    source = job.get("result") if isinstance(job.get("result"), dict) else {}
    result = {}
    phase = source.get("phase")
    if isinstance(phase, str) and phase in PHASES:
        result["phase"] = phase
    for key in ("commit_guard_required", "invalid_state", "accepted", "cancellation_confirmed"):
        if type(source.get(key)) is bool:
            result[key] = source[key]
    mail = source.get("mail")
    if isinstance(mail, dict) and type(mail.get("accepted")) is bool:
        result["mail"] = {"accepted": mail["accepted"]}
    retry_not_before = _timestamp(source.get("retry_not_before"))
    if retry_not_before is not None:
        result["retry_not_before"] = retry_not_before
    status = job.get("status")
    if not isinstance(status, str) or status not in STATUSES:
        status = "failed"
        result["invalid_state"] = True
    return {"id": job["id"], "status": status,
            "will_retry": job.get("will_retry") is True and not result.get("invalid_state", False),
            "reconciliation_required": job.get("reconciliation_required") is True,
            "next_attempt_at": _timestamp(job.get("next_attempt_at")), "result": result}
