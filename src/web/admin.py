"""Account-restricted operational tools; mutations require an administrator CSRF token."""
import json
import logging
import secrets
import time
from urllib.parse import urlsplit
from flask import Blueprint, g, jsonify, render_template, request, session
from src.logging_config import log_event, redact_diagnostic_text
from src.services.admin_access import admin_configuration_error, can_administer
from src.services.editing_state_service import EditingStateError, EditingStateQuarantinedError
from src.services.graph_sync_queue import delivery_uncertain, teams_retry_deadline
from src.services.graph_job_visibility import PHASES
from src.services.diagnostic_views import safe_queue_summary, safe_refresh_status
from src.services.worker_health import configured_status


def register_admin(app, editing, queue, refresh, authenticated_microsoft):
    bp = Blueprint("admin", __name__)
    configuration_error = admin_configuration_error()
    if configuration_error:
        log_event(app.logger, logging.WARNING,
                  "Administração indisponível: configurar GUIDs FS_ADMIN_TENANT_ID e FS_ADMIN_OBJECT_ID.",
                  event="admin_identity_configuration", error_code=configuration_error)

    def allowed():
        return authenticated_microsoft() and can_administer(g.get("current_user"))

    def action_rejected(error):
        reference = g.get("request_id", "indisponível")
        return jsonify(success=False,
                       error=redact_diagnostic_text(error) + " Referência: " + reference + ".",
                       code="admin_action_rejected", reference=reference), 409

    @app.context_processor
    def admin_context():
        return {"special_admin": allowed()}

    def csrf():
        if not session.get("admin_csrf"):
            session["admin_csrf"] = secrets.token_urlsafe(32)
        return session["admin_csrf"]

    @bp.before_request
    def protect_admin():
        if not allowed():
            error_code = admin_configuration_error() or "admin_access_denied"
            message = ("Administração indisponível: o instalador deve conferir FS_ADMIN_TENANT_ID e FS_ADMIN_OBJECT_ID."
                       if error_code != "admin_access_denied" else "Acesso reservado ao administrador.")
            return jsonify(success=False, error=message, error_code=error_code), 403
        if request.method != "GET":
            supplied = request.headers.get("X-Admin-CSRF", "")
            origin = request.headers.get("Origin")
            if (not supplied or not secrets.compare_digest(supplied, session.get("admin_csrf", ""))
                    or (origin and (urlsplit(origin).scheme, urlsplit(origin).netloc) !=
                        (urlsplit(request.host_url).scheme, urlsplit(request.host_url).netloc))
                    or request.headers.get("Sec-Fetch-Site") == "cross-site"):
                return jsonify(success=False, error="Pedido administrativo inválido. Atualize a página."), 403

    @bp.get("/admin")
    def panel():
        return render_template("admin.html", admin_csrf=csrf(), asset_version=app.config["ASSET_VERSION"])

    @bp.after_request
    def private_response(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    def object_json(raw, *, optional=False):
        value = json.loads(raw) if raw is not None else ({} if optional else None)
        if not isinstance(value, dict):
            raise ValueError("Os dados do trabalho são inválidos; requerem revisão.")
        return value

    def safe_job(row):
        invalid = False
        deadline = None
        will_retry = False
        try:
            payload = object_json(row["payload_json"])
            for key in ("mail", "teams", "notification", "_commit_guard"):
                if payload.get(key) is not None and not isinstance(payload[key], dict):
                    raise ValueError()
            result = object_json(row["result_json"], optional=True)
            mail = result.get("mail") or {}
            if not isinstance(mail, dict):
                raise ValueError()
            if ("accepted" in mail and not isinstance(mail["accepted"], bool)
                    or ("phase" in result and not isinstance(result["phase"], str))):
                raise ValueError()
            if row["kind"] == "notify_teams" and "accepted" in result and not isinstance(result["accepted"], bool):
                raise ValueError()
            delivery_unknown = delivery_uncertain(row["kind"], result)
            uncertain = delivery_unknown
            # _send_mail_once persists sending_mail before calling transport.
            # Preparation checkpoints and a missing local commit prove that
            # sending never started; retain uncertainty for attempted legacy
            # jobs without checkpoints or unrecognized terminal outcomes.
            reported_uncertain = uncertain
            deadline = teams_retry_deadline(row["kind"], result)
            has_mail = row["kind"] == "archive_and_mail_local" or bool(payload.get("mail"))
            if has_mail and mail.get("accepted") is not True and result.get("phase") not in ("uploading_archive", "generating_pdf", "pdf_ready", "mail_rejected"):
                uncertain = True
                if result.get("commit_guard_required") is True and not result.get("phase"):
                    # Retry only re-enters held and checks the local commit.
                    # Preserve independent uncertainty, including restore and
                    # reconciliation holds; invalid state is refused below.
                    uncertain = delivery_unknown
                known_not_sent = (result.get("phase") in {
                    "message_ready", "removing_active", "active_removed", "mail_snapshot_invalid"
                } or result.get("commit_guard_required") is True
                    or (not result.get("phase") and row["attempts"] == 0))
                reported_uncertain = reported_uncertain or (
                    row["status"] == "failed" and not row["retryable"] and not known_not_sent)
            invalid = bool(result.get("invalid_state"))
            # Match queue._serialize, not the stricter manual-retry guard.
            will_retry = row["status"] == "failed" and bool(row["retryable"]) and not delivery_unknown and not invalid
        except (TypeError, ValueError):
            result, uncertain, reported_uncertain, invalid = {}, True, True, True
        return {"id": row["id"], "kind": row["kind"], "status": row["status"],
                "attempts": row["attempts"], "updated_at": row["updated_at"],
                "last_error": "Falha registada; consultar a referência do trabalho." if row["last_error"] else None,
                "error_code": "job_invalid_state" if invalid else "job_reconciliation_required" if reported_uncertain else
                              "job_failed" if row["last_error"] and not will_retry else None,
                "phase": result.get("phase") if result.get("phase") in PHASES else None,
                "retry_not_before": deadline,
                "will_retry": will_retry,
                "reconciliation_required": uncertain, "invalid_state": invalid,
                "can_retry": row["status"] == "failed" and not row["retryable"] and not uncertain and not invalid
                    and (deadline is None or time.time() >= deadline)}

    @bp.get("/api/admin/overview")
    def overview():
        jobs = []
        if queue is not None:
            with queue._connect() as connection:
                rows = connection.execute("SELECT * FROM graph_sync_jobs WHERE status != 'complete' ORDER BY updated_at DESC LIMIT 100").fetchall()
            jobs = [safe_job(row) for row in rows]
        # The browser uses only these audit fields. Keep full evidence in the
        # repository; never export details_json or arbitrary nested diagnostics.
        audit = [{"action": entry["action"] if entry["action"] in {
                    "release_sheet", "restore_sheet_access", "retry_job_requested", "retry_job_accepted", "refresh_requested"
                  } else "admin_action",
                  "created_at": entry["created_at"], "actor_label": "Administrador",
                  "target": redact_diagnostic_text(entry["target"]),
                  "reason": redact_diagnostic_text(entry["reason"])}
                 for entry in editing.repository.admin_history()]
        return jsonify(success=True, documents=editing.admin_documents(), jobs=jobs,
                       release=app.config["RELEASE_INFO"],
                       worker=configured_status(app.config["RELEASE_INFO"]),
                       queue_backlog=queue.diagnostic_backlog() if queue is not None else None,
                       queue_summary=safe_queue_summary(queue.summary()) if queue is not None else None,
                       refresh=safe_refresh_status(refresh.status()) if refresh is not None else None,
                       audit=audit)

    def received():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError("Pedido inválido.")
        reason = data.get("reason")
        if not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 400:
            raise ValueError("Indique um motivo entre 3 e 400 caracteres.")
        return data

    @bp.post("/api/admin/documents/<document_id>/release")
    @bp.post("/api/admin/documents/<document_id>/restore")
    def change_access(document_id):
        try:
            data = received()
            version = data.get("lock_version")
            if not isinstance(version, str):
                raise ValueError("Atualize a lista antes de alterar a reserva.")
            restore_user = data.get("user_id") if request.path.endswith("/restore") else None
            if request.path.endswith("/restore") and (not isinstance(restore_user, str) or not restore_user):
                raise ValueError("Utilizador em falta.")
            result = editing.admin_change_access(document_id, g.current_user, data["reason"], version,
                                                  restore_user=restore_user)
            return jsonify(success=True, result=result)
        except EditingStateQuarantinedError as exc:
            return jsonify(success=False, error=redact_diagnostic_text(exc), code="editing_state_quarantined"), 423
        except KeyError:
            return jsonify(success=False, error="Reserva não encontrada."), 404
        except (ValueError, EditingStateError) as exc:
            return action_rejected(exc)

    @bp.post("/api/admin/jobs/<job_id>/retry")
    @bp.post("/api/graph/jobs/<job_id>/retry")
    def retry_job(job_id):
        if queue is None:
            return jsonify(success=False, error="A fila não está ativa."), 409
        try:
            data = received()
            with queue._connect() as connection:
                row = connection.execute("SELECT * FROM graph_sync_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if not safe_job(row)["can_retry"]:
                raise ValueError("Este trabalho requer reconciliação ou já está ativo. Não pode ser repetido.")
            audit = {"actor_email": g.current_user.email, "action": "retry_job_requested",
                     "target": job_id, "reason": data["reason"], "details": {}}
            editing.repository.record_admin_action(audit)
            queue.retry(job_id)
            editing.repository.record_admin_action({**audit, "action": "retry_job_accepted"})
            return jsonify(success=True, job_id=job_id), 202
        except KeyError:
            return jsonify(success=False, error="Trabalho não encontrado."), 404
        except ValueError as exc:
            return action_rejected(exc)

    @bp.post("/api/admin/refresh")
    def refresh_graph():
        if refresh is None:
            return jsonify(success=False, error="SharePoint não está ativo."), 409
        try:
            data = received()
            audit = {"actor_email": g.current_user.email, "action": "refresh_requested",
                     "target": "graph", "reason": data["reason"], "details": {}}
            editing.repository.record_admin_action(audit)
            result = refresh.request_refresh(force=True)
            return jsonify(success=True, refresh=safe_refresh_status(result)), 202
        except ValueError as exc:
            return action_rejected(exc)

    @bp.route("/api/admin/jobs/<job_id>/reconciliation", methods=["GET", "POST"])
    def reconcile_job(job_id):
        if queue is None:
            return jsonify(success=False, error="A fila não está ativa."), 409
        try:
            if request.method == "GET":
                return jsonify(success=True, reconciliation=queue.reconciliation(job_id, actor=g.current_user))
            data = received()
            result = queue.reconcile(job_id, actor=g.current_user, expected_revision=data.get("revision"),
                                     decision=data.get("decision"), reason=data["reason"], evidence=data.get("evidence"))
            return jsonify(success=True, result=result)
        except KeyError:
            return jsonify(success=False, error="Trabalho não encontrado."), 404
        except ValueError as exc:
            return action_rejected(exc)

    app.register_blueprint(bp)
