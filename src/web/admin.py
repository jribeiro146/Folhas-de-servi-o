"""Account-restricted operational tools; mutations require an administrator CSRF token."""
import json
import secrets
import time
from urllib.parse import urlsplit
from flask import Blueprint, g, jsonify, render_template, request, session
from src.services.admin_access import can_administer
from src.services.editing_state_service import EditingStateError
from src.services.graph_sync_queue import delivery_uncertain, teams_retry_deadline


def register_admin(app, editing, queue, refresh, authenticated_microsoft):
    bp = Blueprint("admin", __name__)

    def allowed():
        return authenticated_microsoft() and can_administer(g.get("current_user"))

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
            return jsonify(success=False, error="Acesso reservado ao administrador."), 403
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
            uncertain = delivery_uncertain(row["kind"], result)
            deadline = teams_retry_deadline(row["kind"], result)
            has_mail = row["kind"] == "archive_and_mail_local" or bool(payload.get("mail"))
            if has_mail and mail.get("accepted") is not True and result.get("phase") not in ("generating_pdf", "pdf_ready", "mail_rejected"):
                uncertain = True
            invalid = bool(result.get("invalid_state"))
        except (TypeError, ValueError):
            result, uncertain, invalid = {}, True, True
        return {"id": row["id"], "kind": row["kind"], "status": row["status"],
                "attempts": row["attempts"], "updated_at": row["updated_at"],
                "last_error": row["last_error"], "phase": result.get("phase"),
                "retry_not_before": deadline,
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
        return jsonify(success=True, documents=editing.admin_documents(), jobs=jobs,
                       release=app.config["RELEASE_INFO"],
                       queue_summary=queue.summary() if queue is not None else None,
                       refresh=refresh.status() if refresh is not None else None,
                       audit=editing.repository.admin_history())

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
        except KeyError:
            return jsonify(success=False, error="Reserva não encontrada."), 404
        except (ValueError, EditingStateError) as exc:
            return jsonify(success=False, error=str(exc)), 409

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
            return jsonify(success=False, error=str(exc)), 409

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
            return jsonify(success=True, refresh=result), 202
        except ValueError as exc:
            return jsonify(success=False, error=str(exc)), 409

    app.register_blueprint(bp)
