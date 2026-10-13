"""Human-reviewed provider evidence, bound to one stopped queue revision.

This validates evidence consistency, not the provider's signature. The privileged
administrator must obtain the record from the provider and attest its origin.
No transport is called here; a rejected effect still needs a separate retry.
"""
import base64
import hashlib
import json
import math
import time
import unicodedata
from uuid import UUID

from src.services.admin_access import can_administer
from src.services.file_mutex import file_mutex, FileMutexBusy
from src.logging_config import redact_diagnostic_text


KINDS = {"archive_and_mail_local", "archive_and_remove", "notify_teams", "remove_active"}


def _object(raw):
    value = json.loads(raw) if raw is not None else {}
    if not isinstance(value, dict):
        raise ValueError("Os dados da operação requerem recuperação.")
    return value


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def revision(row):
    return hashlib.sha256(_encoded(dict(row))).hexdigest()


def _text(value, *, minimum=1, maximum=400):
    return isinstance(value, str) and minimum <= len(value.strip()) <= maximum


def _timestamp(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _request_id(value):
    try:
        return str(UUID(value)) if isinstance(value, str) else None
    except ValueError:
        return None


def inspection(row):
    from src.services.graph_sync_queue import delivery_uncertain
    payload, result = _object(row["payload_json"]), _object(row["result_json"])
    if result.get("mail") is not None and not isinstance(result["mail"], dict):
        raise ValueError("O recibo da operação requer recuperação.")
    if result.get("phase") is not None and not isinstance(result["phase"], str):
        raise ValueError("A fase da operação requer recuperação.")
    if payload.get("teams") is not None and not isinstance(payload["teams"], dict):
        raise ValueError("A intenção Teams requer recuperação.")
    history = result.get("reconciliation_history", [])
    if not isinstance(history, list):
        raise ValueError("O histórico da operação requer recuperação.")
    accepted_mail = isinstance(result.get("mail"), dict) and result["mail"].get("accepted") is True
    recovery_hold = any(result.get(key) for key in
                        ("invalid_state", "restore_uncertain", "migration_uncertain", "commit_guard_required"))
    has_mail = row["kind"] == "archive_and_mail_local" or bool(payload.get("mail"))
    legacy_mail = has_mail and row["attempts"] > 0 and not result.get("phase") and not accepted_mail
    needs_review = delivery_uncertain(row["kind"], result) or recovery_hold or legacy_mail
    can_record = row["kind"] in KINDS and row["status"] == "failed" and not row["retryable"] and needs_review
    can_repair = (row["kind"] in {"archive_and_mail_local", "archive_and_remove"}
                  and accepted_mail and bool(payload.get("teams")) and row["status"] in {"failed", "complete"}
                  and not recovery_hold and not result.get("reconciliation_hold"))
    allowed = ["indeterminate"] if can_record else []
    # A known preflight failure must never acquire a new uncertainty hold.
    # Legacy mail without a send checkpoint can retain a note, but lacks the
    # correlation required to prove acceptance/refusal through this workflow.
    provable = ((row["kind"] in {"archive_and_mail_local", "archive_and_remove"}
                 and result.get("phase") in {"sending_mail", "mail_unknown"} and not accepted_mail)
                or (row["kind"] == "notify_teams" and result.get("phase") in {"sending_teams", "teams_unknown"}
                    and result.get("accepted") is not True)
                or (row["kind"] == "remove_active" and result.get("cancellation_started") is True
                    and result.get("cancellation_confirmed") is not True))
    if can_record and not recovery_hold and provable:
        allowed.append("accepted")
        if _request_id(result.get("delivery_request_id")):
            allowed.append("rejected")
    if can_repair:
        allowed.append("repair_teams")
    return {
        "id": row["id"], "kind": row["kind"], "status": row["status"], "revision": revision(row),
        "request_id": _request_id(result.get("delivery_request_id")),
        "can_record": bool(can_record), "can_repair_teams": bool(can_repair), "allowed_decisions": allowed,
        # Evidence, identities and references stay in the private audit; the
        # administrator receives only a bounded, sanitized reason for history.
        "history": [{"decision": event["decision"], "at": event.get("at") if _timestamp(event.get("at")) else None,
                     "applied": event.get("applied") is True,
                     "reason": redact_diagnostic_text(event["reason"][:400]) if isinstance(event.get("reason"), str) else ""}
                    for event in history if isinstance(event, dict) and isinstance(event.get("decision"), str) and event["decision"] in
                    {"accepted", "rejected", "indeterminate", "repair_teams"}],
    }


def _mail(queue, connection, row, result):
    from src.services.graph_mail_service import GraphMailDelivery, MAX_SIMPLE_REQUEST_BYTES
    stored = connection.execute("SELECT * FROM graph_mail_messages WHERE job_id=?", (row["id"],)).fetchone()
    marker = result.get("mail_snapshot")
    if (stored is None or not isinstance(marker, dict) or type(marker.get("format")) is not int or marker["format"] != 1
            or stored["retired_at"] is not None or not isinstance(stored["payload"], bytes)
            or not 0 < len(stored["payload"]) <= MAX_SIMPLE_REQUEST_BYTES
            or type(marker.get("size")) is not int or marker["size"] != len(stored["payload"])
            or stored["byte_count"] != marker["size"] or marker.get("sha256") != stored["sha256"]):
        raise ValueError("A mensagem fixada não tem prova íntegra; manter retenção.")
    delivery = GraphMailDelivery(stored["payload"], stored["sender"], stored["tenant_id"], stored["test_recipient"])
    if queue._mail_digest(row["id"], delivery, stored["created_attempt"]) != stored["sha256"]:
        raise ValueError("A mensagem fixada foi alterada; manter retenção.")
    return delivery, _object(stored["payload"])["message"]


def _addresses(values):
    if not isinstance(values, list) or any(not _text(value) for value in values):
        raise ValueError("A evidência não contém os destinatários esperados.")
    return sorted(value.strip().casefold() for value in values)


def _proof(queue, connection, row, payload, result, decision, evidence, now):
    if (not isinstance(evidence, dict) or evidence.get("reviewed_from_provider") is not True
            or not _text(evidence.get("reference"), minimum=3)
            or not isinstance(evidence.get("record"), dict)):
        raise ValueError("Anexe evidência obtida no fornecedor e confirme a sua origem.")
    encoded = _encoded(evidence)
    if len(encoded) > 65536:
        raise ValueError("A evidência excede o limite de 64 KiB.")
    record = evidence["record"]
    if (record.get("operation_id") != row["id"] or record.get("outcome") != decision
            or not _timestamp(record.get("observed_at"))
            or not row["created_at"] <= record["observed_at"] <= now + 300):
        raise ValueError("A evidência não corresponde à operação, decisão ou período observado.")
    source = evidence.get("source")
    if not _text(record.get("provider_record_id")):
        raise ValueError("Falta a identidade do registo do fornecedor.")
    kind = row["kind"]
    if kind in {"archive_and_mail_local", "archive_and_remove"}:
        if result.get("phase") not in {"sending_mail", "mail_unknown"} or (result.get("mail") or {}).get("accepted") is True:
            raise ValueError("Não existe envio de e-mail incerto nesta operação.")
        if kind == "archive_and_remove" and (result.get("active_removed") is not True or type(result.get("removed_active")) is not bool):
            raise ValueError("A remoção da folha ativa ainda requer reconciliação própria.")
        delivery, message = _mail(queue, connection, row, result)
        headers = message.get("internetMessageHeaders", [])
        if headers != [{"name": "x-sensorpoint-operation-id", "value": row["id"]}]:
            raise ValueError("A mensagem não tem correlação exata com esta operação.")
        if decision == "accepted":
            attachment = message["attachments"][0]
            if (source != "exchange_sent_message" or record.get("is_draft") is not False
                    or str(record.get("sender", "")).strip().casefold() != delivery.sender.strip().casefold()
                    or _addresses(record.get("to")) != _addresses([x["emailAddress"]["address"] for x in message["toRecipients"]])
                    or _addresses(record.get("cc")) != _addresses([x["emailAddress"]["address"] for x in message.get("ccRecipients", [])])
                    or record.get("subject") != message["subject"]
                    or record.get("attachment_sha256") != hashlib.sha256(base64.b64decode(attachment["contentBytes"], validate=True)).hexdigest()):
                raise ValueError("O registo de mensagem enviada não corresponde à mensagem fixada.")
        elif record.get("payload_sha256") != hashlib.sha256(delivery.payload).hexdigest():
            raise ValueError("A recusa não identifica os mesmos bytes de envio.")
    elif kind == "notify_teams":
        if result.get("phase") not in {"sending_teams", "teams_unknown"} or result.get("accepted") is True:
            raise ValueError("Não existe envio Teams incerto nesta operação.")
        notification = payload.get("notification")
        if not isinstance(notification, dict) or not isinstance(notification.get("payload"), dict):
            raise ValueError("A notificação fixada requer recuperação.")
        if record.get("payload_sha256") != hashlib.sha256(_encoded(notification["payload"])).hexdigest():
            raise ValueError("A evidência Teams não corresponde à notificação fixada.")
        if decision == "accepted" and (source != "workflow_history" or type(record.get("http_status")) is not int
                                        or not 200 <= record["http_status"] < 300):
            raise ValueError("O histórico não comprova aceitação pelo Workflow.")
    elif kind == "remove_active":
        plan = payload.get("cancellation")
        if (not isinstance(plan, dict) or plan.get("absent") is not False or not _text(plan.get("source_id"))
                or record.get("item_id") != plan["source_id"] or record.get("drive_id") != plan.get("drive_id")):
            raise ValueError("A evidência não identifica o item do cancelamento.")
        if decision == "accepted":
            expected = queue.graph_service._join_graph_path(queue.graph_service.config.archive_path, "Canceladas") if queue.graph_service else None
            actual = record.get("parent_path")
            if (source != "graph_item" or not isinstance(actual, str) or expected is None
                    or unicodedata.normalize("NFC", actual.strip("/")).casefold() != unicodedata.normalize("NFC", expected.strip("/")).casefold()
                    or record.get("name") != plan.get("destination_name") or not _text(record.get("parent_id"))):
                raise ValueError("O item não está confirmado no destino do cancelamento.")
        elif record.get("if_match") != plan.get("source_etag"):
            raise ValueError("A recusa não identifica a versão do cancelamento.")
    else:
        raise ValueError("Tipo de operação sem reconciliação suportada.")
    if decision == "rejected":
        status = record.get("http_status")
        current_request = _request_id(result.get("delivery_request_id"))
        if current_request is None or _request_id(record.get("request_id")) != current_request:
            raise ValueError("A recusa não identifica o último pedido externo. Manter retenção.")
        if source != "provider_response" or type(status) is not int or not 400 <= status < 500 or status in {408, 425}:
            raise ValueError("O registo não comprova uma recusa anterior à aceitação.")
        if kind == "remove_active" and status not in {409, 412}:
            raise ValueError("O conflito de cancelamento continua por esclarecer.")
        if status == 429 and (not _timestamp(record.get("retry_not_before")) or record["retry_not_before"] < record["observed_at"]):
            raise ValueError("Falta o prazo indicado pelo fornecedor para esta recusa.")
    return {"source": source, "reference": evidence["reference"], "sha256": hashlib.sha256(encoded).hexdigest(),
            "record": record}


def reconcile(queue, job_id, *, actor, expected_revision, decision, reason, evidence=None):
    if not can_administer(actor):
        raise PermissionError("Acesso reservado ao administrador.")
    if not isinstance(decision, str) or decision not in {"accepted", "rejected", "indeterminate", "repair_teams"} or not _text(reason, minimum=3):
        raise ValueError("Indique decisão e motivo válidos.")
    now = time.time()
    try:
        # A stopped DB row alone is not permission to race a live transport.
        with file_mutex(queue._worker_mutex_path), queue._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM graph_sync_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            view = inspection(row)
            if expected_revision != view["revision"]:
                raise ValueError("A operação mudou. Atualize o diagnóstico antes de decidir.")
            payload, result = _object(row["payload_json"]), _object(row["result_json"])
            if decision not in view["allowed_decisions"]:
                raise ValueError("A operação está ativa ou não admite esta decisão.")
            if decision != "indeterminate" and any(result.get(key) for key in
                    ("invalid_state", "restore_uncertain", "migration_uncertain", "commit_guard_required")):
                raise ValueError("A operação requer recuperação própria; a retenção mantém-se.")
            if decision == "repair_teams" and result.get("reconciliation_hold"):
                raise ValueError("A operação continua retida para reconciliação.")
            proof = None
            if decision in {"accepted", "rejected"}:
                try:
                    proof = _proof(queue, connection, row, payload, result, decision, evidence, now)
                except (KeyError, IndexError, TypeError, OverflowError):
                    raise ValueError("A evidência ou a mensagem fixada está incompleta; manter retenção.") from None
            event = {"at": now, "decision": decision, "reason": reason.strip(),
                     "actor": {"tenant_id": actor.tenant_id, "object_id": actor.id},
                     "previous_revision": expected_revision, "previous_phase": result.get("phase"),
                     "applied": decision != "indeterminate", "evidence": proof}
            status, error = row["status"], row["last_error"]
            if decision == "indeterminate":
                result["reconciliation_hold"] = True
            elif decision == "repair_teams":
                notification = payload.get("teams") or {}
                card = notification.get("payload")
                if (not isinstance(card, dict) or card.get("type") != "message"
                        or not isinstance(card.get("attachments"), list) or not card["attachments"]
                        or any(not isinstance(part, dict) for part in card["attachments"])):
                    raise ValueError("A intenção Teams não está completa; requer recuperação.")
                queue._attach_teams_child(connection, job_id, payload, result)
                event["applied"] = not result.get("teams_child_conflict", False)
            elif decision in {"accepted", "rejected"}:
                result.pop("reconciliation_hold", None)
                accepted = decision == "accepted"
                if row["kind"] == "notify_teams":
                    result.update(accepted=accepted, phase="teams_accepted" if accepted else "teams_rejected")
                elif row["kind"] == "remove_active":
                    result.update(cancellation_confirmed=accepted, cancellation_started=accepted,
                                  phase="cancellation_confirmed" if accepted else "cancellation_rejected")
                    if accepted:
                        result.update(removed_active=True, remote_preserved=True, remote_id=payload["cancellation"]["source_id"])
                else:
                    result.update(mail={"accepted": accepted, "reconciled": True}, phase="mail_accepted" if accepted else "mail_rejected")
                    if accepted:
                        queue._attach_teams_child(connection, job_id, payload, result)
                        connection.execute("UPDATE graph_mail_messages SET payload=NULL,retired_at=? WHERE job_id=?", (now, job_id))
                if not accepted:
                    record = proof["record"]
                    result["http_status"] = record["http_status"]
                    if record["http_status"] == 429:
                        result["retry_not_before"] = max(result.get("retry_not_before") or 0, record["retry_not_before"])
                        result["reconciled_retry_deadline"] = True
                status, error = ("complete", None) if accepted else ("failed", "Recusa confirmada por reconciliação administrativa.")
            history = result.setdefault("reconciliation_history", [])
            history.append(event)
            # Decision and audit are one SQLite commit. Never queue a replay or
            # delete the fixed message on an indeterminate/rejected decision.
            connection.execute("UPDATE graph_sync_jobs SET result_json=?,status=?,retryable=0,last_error=?,updated_at=? WHERE id=?",
                               (json.dumps(result, ensure_ascii=False, allow_nan=False), status, error, now, job_id))
            connection.commit()
            return {"id": job_id, "decision": decision, "applied": event["applied"], "status": status}
    except FileMutexBusy:
        raise ValueError("Existe um worker ativo. Aguarde que termine antes de reconciliar.") from None
