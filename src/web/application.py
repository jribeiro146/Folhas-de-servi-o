"""Aplicação Flask e API da webapp de Folhas de Serviço."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import re
import secrets
import time
import uuid
from functools import wraps
from contextlib import nullcontext
from pathlib import Path

from flask import Flask, g, jsonify, redirect, render_template, request, send_from_directory, session, url_for

from src.config import (
    APP_DATA_DIR,
    AUTH_PROVIDER,
    GRAPH_QUEUE_IN_WEB,
    GRAPH_SHAREPOINT_HOSTNAME,
    GRAPH_WORKS_CACHE_SECONDS,
    GRAPH_WORKS_PATH,
    MAIL_ENABLED,
    MICROSOFT_AUTH_REDIRECT_URI,
    LOG_REQUESTS,
    TEAMS_NOTIFICATIONS_ENABLED,
    STORAGE_BACKEND,
)
from src.document_schema import (
    DOCUMENT_REQUIRED_FIELDS,
    TECHNICIAN_REQUIRED_FIELDS,
    SIGNATURE_REQUIRED_FIELDS,
    EQUIPMENT_OPTIONS,
    SERVICE_TYPE_OPTIONS,
    TECHNICIAN_OPTIONS,
    document_from_excel_and_extra,
    document_invalid_fields,
    document_missing_required_fields,
    get_primary_technician_name,
    get_technician_initials,
    document_to_excel_form,
    normalize_document_payload,
    strip_signature_payload,
)
from src.field_map import FIELD_MAP, FIELDS_BY_GROUP
from src.services.file_diagnostics import diagnostic, file_fields, lookup_fields
from src.logging_config import (
    bind_request_context,
    log_event,
    reset_request_context,
)
from src.services.archive_service import ArchiveService
from src.services.finalization_service import FinalizationService
from src import maintenance_schema as maintenance
from src.services.maintenance_artifact_service import build_maintenance_bundle, render_maintenance_document
from src.services.maintenance_access import can_access_maintenance
from src.services.maintenance_private_service import MaintenancePrivateService
from src.services.document_style_service import load_document_assets
from src.services.runtime_safety import runtime_mode
from src.services.local_changes import bundle_guard, mark_dirty
from src.services.file_mutex import FileMutexBusy
from src.services.document_artifact_service import DocumentArtifactService
from src.services.document_data_service import DocumentDataService
from src.services.editing_state_service import (
    EditingStateError,
    EditingSessionMetadataError,
    EditingStateService,
    EditorIdentity,
    LeaseConflictError,
    LeaseRequiredError,
    OperationInProgressError,
    RevisionConflictError,
)
from src.services.excel_service import ExcelService, ExcelValidationError
from src.services.file_service import FileService
from src.services.graph_mail_service import (
    GraphMailConfigurationError,
    GraphMailError,
    GraphMailService,
)
from src.services.graph_storage_service import GraphConflictError, GraphStorageError, GraphStorageService
from src.services.graph_sync_coordinator import GraphRefreshCoordinator
from src.services.graph_sync_queue import GraphSyncQueue
from src.services.local_pdf_service import LocalPdfService
from src.services.microsoft_auth_service import MicrosoftAuthError, MicrosoftAuthService
from src.services.photo_attachment_service import PhotoAttachmentError, PhotoAttachmentService
from src.services.signature_service import CLIENT_SIGNATURE_LABEL, SignatureService
from src.services.teams_notification_service import (
    TeamsNotificationConfigurationError,
    TeamsNotificationError,
    TeamsNotificationService,
)

from src.services.work_folder_service import (
    WorkFolderAmbiguousError,
    WorkFolderInvalidNumberError,
    WorkFolderNotFoundError,
    WorkFolderService,
    WorkFolderUnsafeUrlError,
)

ACTIVE_AUTH_PROVIDER = "microsoft" if AUTH_PROVIDER == "microsoft" else "none"
AUTH_ENABLED = ACTIVE_AUTH_PROVIDER == "microsoft"
INTERNAL_OBSERVATIONS_KEY = "_internal_observations"
QUIET_HTTP_ENDPOINTS = {
    "acquire_file_lease",
    "autosave_file",
    "graph_job_status",
    "graph_status",
    "heartbeat_file_lease",
}


_LOCAL_SECRET = secrets.token_hex(32)


def _load_secret_key() -> str:
    configured = os.environ.get("FS_SECRET_KEY", "").strip()
    if configured and len(configured) >= 32 and configured != "dev-local-secret":
        return configured
    if runtime_mode() == "production" or AUTH_ENABLED:
        raise ValueError("FS_SECRET_KEY deve conter pelo menos 32 caracteres aleatórios.")
    if configured:
        raise ValueError("FS_SECRET_KEY configurada é demasiado curta.")
    return _LOCAL_SECRET


def create_app(
    file_service: FileService | None = None,
    archive_service: ArchiveService | None = None,
    graph_service: GraphStorageService | None = None,
    mail_service: GraphMailService | None = None,
    microsoft_auth_service: MicrosoftAuthService | None = None,
    editing_state_service: EditingStateService | None = None,
    graph_refresh_coordinator: GraphRefreshCoordinator | None = None,
    graph_sync_queue: GraphSyncQueue | None = None,
    local_pdf_service: LocalPdfService | None = None,
    teams_notification_service: TeamsNotificationService | None = None,
    work_folder_service: WorkFolderService | None = None,
    test_editor_identity: EditorIdentity | None = None,
) -> Flask:
    """Cria a aplicação Flask com dependências injetáveis para testes."""
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["ASSET_VERSION"] = dt.datetime.now().strftime("%Y%m%d%H%M%S")
    app.config["SYNTHETIC_TEST_VERSION"] = runtime_mode() == "test" and os.environ.get("FS_TEST_SYNTHETIC") == "true"
    # Demo only: no operational queues or transports may be attached to this feature.
    app.config["MAINTENANCE_DEMO"] = app.config["SYNTHETIC_TEST_VERSION"] and STORAGE_BACKEND == "local" and not any((MAIL_ENABLED, TEAMS_NOTIFICATIONS_ENABLED, GRAPH_QUEUE_IN_WEB, graph_service, mail_service, graph_sync_queue, teams_notification_service))
    app.config["MAINTENANCE_ENABLED"] = app.config["MAINTENANCE_DEMO"] or (
        runtime_mode() == "production"
        and AUTH_ENABLED
        and STORAGE_BACKEND == "graph"
        and os.environ.get("FS_MAINTENANCE_ENABLED", "true").strip().casefold() == "true"
    )
    if test_editor_identity is not None and not (
        app.config["MAINTENANCE_DEMO"] and not AUTH_ENABLED and ACTIVE_AUTH_PROVIDER == "none"
    ):
        raise ValueError("O técnico simulado só pode ser usado na demo local isolada, sem autenticação ou integrações reais.")
    app.secret_key = _load_secret_key()
    if runtime_mode() != "production":
        # Browsers share cookies between localhost ports. Separate development
        # instances use different signing keys and must not replace each other's
        # session (which also owns the draft's editing lease).
        session_namespace = hashlib.sha256(app.secret_key.encode("utf-8")).hexdigest()[:16]
        app.config["SESSION_COOKIE_NAME"] = f"sensorpoint_fs_{session_namespace}"
    @app.context_processor
    def maintenance_context():
        allowed = maintenance_access_allowed()
        return {
            "maintenance_definition": maintenance.DEFINITION if allowed else {},
            "maintenance_enabled_for_user": allowed,
        }

    app.config.update(
        SESSION_COOKIE_SECURE=runtime_mode() == "production",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=64 * 1024 * 1024,
        PERMANENT_SESSION_LIFETIME=dt.timedelta(hours=14),
    )

    file_service = file_service or FileService()
    archive_service = archive_service or ArchiveService()
    graph_service = graph_service or (GraphStorageService() if STORAGE_BACKEND == "graph" else None)
    work_folder_service = work_folder_service or WorkFolderService(
        graph_service if graph_service is not None else GraphStorageService(),
        folder_path=GRAPH_WORKS_PATH,
        allowed_hostname=GRAPH_SHAREPOINT_HOSTNAME,
        cache_seconds=GRAPH_WORKS_CACHE_SECONDS,
    )
    mail_service = mail_service or (GraphMailService() if MAIL_ENABLED else None)
    teams_notification_service = teams_notification_service or (
        TeamsNotificationService()
        if TEAMS_NOTIFICATIONS_ENABLED and mail_service is not None
        else None
    )
    local_pdf_service = local_pdf_service or (
        LocalPdfService() if mail_service is not None and graph_service is None else None
    )
    microsoft_auth_service = microsoft_auth_service or (
        MicrosoftAuthService() if ACTIVE_AUTH_PROVIDER == "microsoft" else None
    )

    editing_state_service = editing_state_service or EditingStateService()
    private_maintenance = MaintenancePrivateService(APP_DATA_DIR / "sadi")
    finalization_service = FinalizationService(archive_service, APP_DATA_DIR / "finalizing")
    graph_refresh_coordinator = graph_refresh_coordinator or (
        GraphRefreshCoordinator(
            graph_service,
            file_service.directory,
            on_complete=file_service.invalidate_cache,
        )
        if graph_service is not None else None
    )
    graph_sync_queue = graph_sync_queue or (
        GraphSyncQueue(
            graph_service,
            APP_DATA_DIR / "graph-sync.sqlite3",
            mail_service=mail_service,
            local_pdf_service=local_pdf_service,
            teams_notification_service=teams_notification_service,
            auto_start=GRAPH_QUEUE_IN_WEB,
        )
        if graph_service is not None or mail_service is not None else None
    )

    @app.before_request
    def begin_request_logging():
        supplied_request_id = str(request.headers.get("X-Request-ID") or "").strip()
        request_id = (
            supplied_request_id
            if re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", supplied_request_id)
            else uuid.uuid4().hex
        )
        g.request_started_at = time.perf_counter()
        g.request_log_tokens = bind_request_context(
            request_id,
            request.method,
            request.url_rule.rule if request.url_rule is not None else "<unmatched>",
        )
        g.request_id = request_id

    @app.after_request
    def complete_request_logging(response):
        request_id = str(g.get("request_id") or "")
        if request_id:
            response.headers["X-Request-ID"] = request_id
        if LOG_REQUESTS and request.endpoint != "static":
            started_at = float(g.get("request_started_at") or time.perf_counter())
            duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            status_code = int(response.status_code)
            endpoint = request.endpoint or "unknown"
            if status_code >= 500:
                level = logging.ERROR
            elif status_code >= 400:
                level = (
                    logging.DEBUG
                    if endpoint == "acquire_file_lease" and status_code == 409
                    else logging.WARNING
                )
            else:
                level = logging.DEBUG if endpoint in QUIET_HTTP_ENDPOINTS else logging.INFO
            log_event(
                app.logger,
                level,
                "Pedido HTTP concluído.",
                event="http_request_completed",
                endpoint=endpoint,
                status_code=status_code,
                duration_ms=duration_ms,
            )
        return response

    @app.teardown_request
    def clear_request_logging(_error):
        reset_request_context(g.pop("request_log_tokens", None))

    @app.after_request
    def apply_cache_headers(response):
        if request.path.startswith(("/api/", "/work-folder/")) or request.path in {
            "/",
            "/login",
            "/service-worker.js",
            "/static/js/field-app.js",
            "/static/js/mail-job-monitor.js",
            "/static/js/document-editor.js",
            "/static/js/editing-coordinator.js",
            "/static/css/auth.css",
            "/static/css/field-app.css",
            "/static/css/document-editor.css",
            "/static/css/editing-state.css",
            "/static/css/service-document.css",
        }:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    def json_error(message: str, status: int = 400):
        return jsonify({"success": False, "error": message}), status

    def retire_committed_source(document_id, operation_id, context):
        source = Path(str(context.get("source_path") or ""))
        archived = Path(str(context.get("archived_path") or ""))
        if not file_service._contained(source) or not finalization_service.owns(archived, f"{document_id}:{operation_id}"):
            return
        if source.exists() and context.get("source_fingerprint") != editing_state_service._fingerprint(source):
            return
        finalization_service.retire_source(source)
        if file_service.get_file_by_name(source.stem) is None:
            editing_state_service.update_operation_context(document_id, operation_id, source_retired=True)
        file_service.invalidate_cache(source)

    def recover_committed_finalizations():
        if request.endpoint not in {"index", "get_files", "bootstrap_file_editor", "get_file_data"}:
            return
        for document_id, operation_id, context in editing_state_service.finalizations_to_retire():
            try:
                with editing_state_service.operation_guard(document_id, current_editor_identity()):
                    source = Path(context["source_path"])
                    with bundle_guard(source.parent):
                        retire_committed_source(document_id, operation_id, context)
            except (OSError, EditingStateError, FileMutexBusy):
                continue

    @app.errorhandler(413)
    def request_too_large(_error):
        return json_error("O pedido excede o limite máximo permitido.", 413)

    @app.errorhandler(FileMutexBusy)
    def shared_state_busy(_error):
        response = jsonify({"success": False, "code": "temporarily_busy",
                            "error": "Atualização em curso. Tente novamente dentro de momentos."})
        response.status_code = 503
        response.headers["Retry-After"] = "2"
        return response

    def current_editor_identity() -> EditorIdentity:
        current_user = g.get("current_user")
        if current_user is not None:
            return EditorIdentity(
                id=str(current_user.id),
                display_name=str(current_user.display_name),
            )

        # The isolated, single-person demo may inject one fictional technician
        # across browsers. Normal anonymous and authenticated ownership is unchanged.
        if test_editor_identity is not None:
            return test_editor_identity

        anonymous_id = str(session.get("anonymous_editor_id") or "")
        if not anonymous_id:
            anonymous_id = secrets.token_urlsafe(18)
            session["anonymous_editor_id"] = anonymous_id
            session.permanent = True
        return EditorIdentity(id=f"local:{anonymous_id}", display_name="Técnico local")

    def editing_error_response(error: Exception):
        if isinstance(error, EditingSessionMetadataError):
            log_event(
                app.logger,
                logging.WARNING,
                "Pedido sem metadados da sessão de edição.",
                event="editing_session_metadata_missing",
                missing_fields=error.missing,
            )
            return jsonify({
                "success": False,
                "error": str(error),
                "code": "editing_session_required",
            }), 409
        if isinstance(error, LeaseConflictError):
            return jsonify({
                "success": False,
                "error": str(error),
                "code": "lease_conflict",
                "editing": error.snapshot,
            }), 423
        if isinstance(error, LeaseRequiredError):
            return jsonify({
                "success": False,
                "error": str(error),
                "code": "lease_required",
                "editing": error.snapshot,
            }), 423
        if isinstance(error, RevisionConflictError):
            return jsonify({
                "success": False,
                "error": str(error),
                "code": "revision_conflict",
                "editing": error.snapshot,
            }), 409
        if isinstance(error, OperationInProgressError):
            return jsonify({
                "success": False,
                "error": str(error),
                "code": "operation_in_progress",
            }), 409
        return json_error(str(error), 400)

    def extract_edit_metadata(
        payload: dict[str, object],
        *,
        require_idempotency: bool = False,
    ) -> dict[str, object]:
        raw = payload.pop("_edit", {})
        metadata = dict(raw) if isinstance(raw, dict) else {}
        required = ["document_id", "client_id", "lease_token", "base_revision"]
        missing = [key for key in required if not str(metadata.get(key) or "").strip()]
        if require_idempotency and not str(metadata.get("idempotency_key") or "").strip():
            missing.append("idempotency_key")
        if missing:
            raise EditingSessionMetadataError(missing)
        try:
            metadata["base_revision"] = int(metadata["base_revision"])
        except (TypeError, ValueError) as exc:
            raise EditingStateError("Revisão base inválida.") from exc
        return metadata

    def exclusive_operation(view):
        @wraps(view)
        def guarded(*args, **kwargs):
            payload = request.get_json(silent=True) if request.is_json else None
            if payload is None:
                try:
                    payload = json.loads(request.form.get("document") or "{}")
                except (TypeError, ValueError):
                    payload = {}
            metadata = payload.get("_edit") if isinstance(payload, dict) else None
            document_id = str(metadata.get("document_id") or "") if isinstance(metadata, dict) else ""
            if not document_id:
                return view(*args, **kwargs)
            try:
                path = file_service.get_file_by_name(str(kwargs.get("name") or ""))
                with editing_state_service.operation_guard(
                    document_id, current_editor_identity(),
                    allow_foreign_owner=can_edit_foreign_sadi_draft(path),
                ):
                    guard = bundle_guard(path.parent) if path and path.parent.name == path.stem else nullcontext()
                    with guard:
                        return view(*args, **kwargs)
            except FileMutexBusy:
                return editing_error_response(OperationInProgressError("A folha está a ser sincronizada. Tente novamente."))
            except EditingStateError as exc:
                return editing_error_response(exc)
        return guarded

    def resolve_editing_document_id(path: Path, client_id: str) -> str:
        if file_service.is_draft_file(path):
            return editing_state_service.resolve_document_id(path)
        return editing_state_service.resolve_private_workspace_id(
            path,
            current_editor_identity(),
            client_id,
        )

    def validate_document_identity(path: Path, metadata: dict[str, object]) -> str:
        document_id = str(metadata["document_id"])
        expected_id = resolve_editing_document_id(path, str(metadata["client_id"]))
        if document_id != expected_id:
            raise RevisionConflictError(editing_state_service.snapshot_document(expected_id))
        return document_id

    def wants_json_response() -> bool:
        return request.path.startswith("/api/")

    def safe_next_url(value: str | None) -> str:
        candidate = str(value or "").strip()
        if candidate.startswith("/") and not candidate.startswith("//"):
            return candidate
        return url_for("index")

    def microsoft_redirect_uri() -> str:
        return MICROSOFT_AUTH_REDIRECT_URI or url_for("microsoft_auth_callback", _external=True)

    def friendly_graph_login_error(error: GraphStorageError) -> str:
        detail = str(error)
        if "AADSTS7000215" in detail or "invalid_client" in detail:
            return (
                "Não foi possível ligar ao Microsoft Graph. "
                "Verifique se o GRAPH_CLIENT_SECRET contém o Secret Value do Entra ID, "
                "não o Secret ID."
            )
        return f"Microsoft Graph indisponível: {detail}"

    def set_current_user() -> None:
        if ACTIVE_AUTH_PROVIDER == "microsoft":
            g.current_user = (
                microsoft_auth_service.user_from_session(session.get("microsoft_user"))
                if microsoft_auth_service else None
            )
            return

        g.current_user = None

    def maintenance_access_allowed() -> bool:
        if not app.config["MAINTENANCE_ENABLED"]:
            return False
        if app.config["MAINTENANCE_DEMO"] and not AUTH_ENABLED:
            return True
        return can_access_maintenance(g.get("current_user"))

    def can_edit_foreign_sadi_draft(path: Path | None) -> bool:
        """The two SADI editors may work on an applicable draft from another technician."""
        if not path or not maintenance_access_allowed() or not file_service.is_draft_file(path):
            return False
        return maintenance.applicable(DocumentDataService(path).read())

    def trusted_maintenance(path: Path) -> list[dict]:
        if not app.config["MAINTENANCE_ENABLED"]:
            return []
        snapshot = editing_state_service.snapshot(path)
        autosaved = snapshot.get("server_document")
        if isinstance(autosaved, dict) and "maintenance_checklists" in autosaved:
            return autosaved["maintenance_checklists"]
        if not app.config["MAINTENANCE_DEMO"]:
            document_id = editing_state_service.resolve_document_id(path)
            return private_maintenance.read(document_id)
        return DocumentDataService(path).read().get("maintenance_checklists") or []

    def normalize_document_for_write(payload, path: Path):
        document = normalize_document_for_file(payload, path)
        if app.config["MAINTENANCE_ENABLED"] and not maintenance_access_allowed():
            document["maintenance_checklists"] = maintenance.normalize_sites(trusted_maintenance(path))
            maintenance.clear_invalid_signatures(document, app.secret_key)
        return document

    def public_document(document: dict) -> dict:
        if runtime_mode() == "production":
            return {key: value for key, value in document.items() if key != "maintenance_checklists"}
        return document

    def hide_maintenance(value):
        if isinstance(value, list):
            return [hide_maintenance(item) for item in value]
        if isinstance(value, dict):
            return {key: hide_maintenance(item) for key, item in value.items()
                    if key not in {"maintenance_checklists", "maintenance_bundle_url"}}
        return value

    @app.after_request
    def redact_maintenance_json(response):
        if response.direct_passthrough or not response.is_json or maintenance_access_allowed():
            return response
        body = response.get_json(silent=True)
        if isinstance(body, (dict, list)):
            response.set_data(json.dumps(hide_maintenance(body), ensure_ascii=False))
        return response

    @app.before_request
    def require_login():
        set_current_user()

        if not AUTH_ENABLED:
            return None

        public_endpoints = {
            "login",
            "microsoft_auth_start",
            "microsoft_auth_callback",
            "static",
            "manifest",
            "service_worker",
        }
        if request.endpoint in public_endpoints:
            return None

        if g.current_user:
            return None

        if wants_json_response():
            return json_error("Sessão expirada. Faça login novamente.", 401)

        return redirect(url_for("login", next=request.full_path if request.query_string else request.path))

    app.before_request(recover_committed_finalizations)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if not AUTH_ENABLED:
            return redirect(url_for("index"))

        if g.get("current_user"):
            return redirect(safe_next_url(request.args.get("next")))

        if ACTIVE_AUTH_PROVIDER == "microsoft":
            return render_template(
                "login.html",
                auth_provider="microsoft",
                error=request.args.get("error"),
                next_url=safe_next_url(request.args.get("next")),
                asset_version=app.config["ASSET_VERSION"],
            )

        return redirect(url_for("index"))

    @app.route("/auth/microsoft")
    def microsoft_auth_start():
        if ACTIVE_AUTH_PROVIDER != "microsoft" or microsoft_auth_service is None:
            return redirect(url_for("login"))

        if graph_service is not None:
            try:
                graph_service.test_connection()
            except GraphStorageError as exc:
                log_event(
                    app.logger,
                    logging.ERROR,
                    "Pré-validação Graph do login Microsoft falhou.",
                    event="auth_graph_preflight_failed",
                    exc_info=True,
                )
                return redirect(url_for("login", error=friendly_graph_login_error(exc)))

        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        session["microsoft_auth_state"] = state
        session["microsoft_auth_nonce"] = nonce
        session["microsoft_auth_next"] = safe_next_url(request.args.get("next"))

        try:
            authorization_url = microsoft_auth_service.build_authorization_url(
                redirect_uri=microsoft_redirect_uri(),
                state=state,
                nonce=nonce,
            )
        except MicrosoftAuthError as exc:
            log_event(
                app.logger,
                logging.ERROR,
                "Não foi possível construir o pedido de login Microsoft.",
                event="auth_start_failed",
                error_type=type(exc).__name__,
            )
            return redirect(url_for("login", error=str(exc)))

        return redirect(authorization_url)

    @app.route("/auth/microsoft/callback")
    def microsoft_auth_callback():
        if ACTIVE_AUTH_PROVIDER != "microsoft" or microsoft_auth_service is None:
            return redirect(url_for("login"))

        error = request.args.get("error_description") or request.args.get("error")
        if error:
            log_event(
                app.logger,
                logging.WARNING,
                "O fornecedor Microsoft recusou o login.",
                event="auth_provider_rejected",
                provider_error_code=request.args.get("error"),
            )
            return redirect(url_for("login", error=error))

        state = request.args.get("state")
        code = request.args.get("code")
        expected_state = session.pop("microsoft_auth_state", None)
        nonce = session.pop("microsoft_auth_nonce", None)
        next_url = safe_next_url(session.pop("microsoft_auth_next", None))

        if not state or state != expected_state or not code or not nonce:
            log_event(
                app.logger,
                logging.WARNING,
                "Callback Microsoft sem estado de sessão válido.",
                event="auth_callback_invalid_state",
            )
            return redirect(url_for("login", error="Sessão Microsoft inválida. Tente novamente."))

        try:
            user = microsoft_auth_service.authenticate_code(
                code=code,
                redirect_uri=microsoft_redirect_uri(),
                nonce=nonce,
            )
        except MicrosoftAuthError as exc:
            log_event(
                app.logger,
                logging.ERROR,
                "Autenticação Microsoft falhou.",
                event="auth_callback_failed",
                error_type=type(exc).__name__,
            )
            return redirect(url_for("login", error=str(exc)))

        session.clear()
        session.permanent = True
        session["microsoft_user"] = microsoft_auth_service.user_to_session(user)
        return redirect(next_url)

    @app.route("/logout", methods=["POST"])
    def logout():
        identity = current_editor_identity()
        editing_state_service.release_user_leases(identity.id)
        session.clear()
        return redirect(url_for("login"))

    def serialize_payload(value):
        if isinstance(value, dt.datetime):
            return value.strftime("%Y-%m-%d")
        if isinstance(value, dt.date):
            return value.strftime("%Y-%m-%d")
        if isinstance(value, dt.time):
            return value.strftime("%H:%M")
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {key: serialize_payload(item) for key, item in value.items()}
        if isinstance(value, list):
            return [serialize_payload(item) for item in value]
        if isinstance(value, tuple):
            return [serialize_payload(item) for item in value]
        return value

    def pop_internal_observations(payload: dict[str, object]) -> str:
        return str(payload.pop(INTERNAL_OBSERVATIONS_KEY, "") or "").strip()

    def write_internal_observations(path, observations: str) -> Path | None:
        if not observations:
            return None

        notes_path = Path(path).with_name(f"{Path(path).stem}__observacoes_internas.txt")
        notes_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = notes_path.with_name(f".{notes_path.name}.{secrets.token_hex(6)}.tmp")
        try:
            temp_path.write_text(f"{observations}\n", encoding="utf-8")
            os.replace(str(temp_path), str(notes_path))
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
        return notes_path

    def serialize_file_entry(entry: dict[str, object]) -> dict[str, object]:
        serialized = dict(entry)
        return serialize_payload(serialized)

    def parse_commit_request() -> tuple[dict[str, object], list[object], list[str]]:
        if request.is_json:
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict) or not payload:
                raise PhotoAttachmentError("Sem dados")
            return dict(payload), [], []

        raw_document = str(request.form.get("document") or "").strip()
        if not raw_document:
            raise PhotoAttachmentError("Sem dados")
        try:
            payload = json.loads(raw_document)
        except json.JSONDecodeError as exc:
            raise PhotoAttachmentError("Documento inválido.") from exc
        if not isinstance(payload, dict) or not payload:
            raise PhotoAttachmentError("Documento inválido.")

        raw_removed = str(request.form.get("removed_photo_ids") or "[]")
        try:
            removed_ids = json.loads(raw_removed)
        except json.JSONDecodeError as exc:
            raise PhotoAttachmentError("Lista de fotografias removidas inválida.") from exc
        if not isinstance(removed_ids, list) or not all(
            isinstance(value, str) for value in removed_ids
        ):
            raise PhotoAttachmentError("Lista de fotografias removidas inválida.")

        return dict(payload), list(request.files.getlist("photos")), removed_ids

    def photos_for_response(path: Path) -> list[dict[str, object]]:
        photos: list[dict[str, object]] = []
        for photo in PhotoAttachmentService(path).list():
            item = dict(photo)
            item["url"] = url_for(
                "get_file_photo",
                name=path.stem,
                photo_id=str(photo["id"]),
            )
            photos.append(item)
        return photos

    def normalize_document_for_file(
        payload: dict[str, object] | None,
        file_name: str | Path,
    ) -> dict[str, object]:
        """Fixa a identidade da folha pelo prefixo canónico do nome do ficheiro."""
        document = normalize_document_payload(payload)
        canonical_number = FileService.service_number_from_name(file_name)
        if canonical_number:
            document["service_number"] = canonical_number
        if app.config["MAINTENANCE_ENABLED"]:
            maintenance.clear_invalid_signatures(document, app.secret_key)
        return document

    @app.route("/api/file/<name>/maintenance/validate", methods=["POST"])
    def validate_maintenance(name):
        if not maintenance_access_allowed():
            return json_error("Checklist não disponível.", 404)
        path = file_service.get_file_by_name(name)
        if not path or not file_service.is_draft_file(path):
            return json_error("Crie primeiro um rascunho.", 409)
        received = request.get_json(silent=True) or {}
        document = normalize_document_for_file(received.get("document"), path)
        return jsonify(success=True, errors=maintenance.document_errors(document, app.secret_key),
                       sites=[{"id": site["id"], "errors": maintenance.site_errors(site),
                               "waived": {role: maintenance.signature_waived(site, role)
                                          for role in ("technician", "customer")},
                               "signed": {role: maintenance.signature_valid(app.secret_key, document, site, role)
                                          for role in ("technician", "customer")}}
                              for site in document["maintenance_checklists"]])

    @app.route("/api/file/<name>/maintenance/sign", methods=["POST"])
    def sign_maintenance(name):
        if not maintenance_access_allowed():
            return json_error("Checklist não disponível.", 404)
        path = file_service.get_file_by_name(name)
        if not path or not file_service.is_draft_file(path):
            return json_error("Crie primeiro um rascunho.", 409)
        received = request.get_json(silent=True) or {}
        try:
            metadata = extract_edit_metadata(received)
            document_id = validate_document_identity(path, metadata)
            editing_state_service.renew_lease(document_id, current_editor_identity(),
                                            str(metadata["client_id"]), str(metadata["lease_token"]))
            document = normalize_document_for_file(received.get("document"), path)
            if not maintenance.applicable(document):
                raise ValueError("Selecione Manutenção e SADI.")
            matches = [site for site in document["maintenance_checklists"] if site["id"] == received.get("site_id")]
            if len(matches) != 1:
                raise ValueError("Local inválido ou repetido.")
            site = matches[0]
            signature = maintenance.make_signature(app.secret_key, document, site, received.get("role"),
                                                   received.get("name"), received.get("date"), received.get("image"))
            return jsonify(success=True, signature=signature)
        except EditingStateError as exc:
            return editing_error_response(exc)
        except ValueError as exc:
            return json_error(str(exc), 400)

    def demo_bundle(bundle):
        from src.services import archive_service as archive_module
        root = archive_module.EXCEL_ARQUIVADAS_DIR.resolve()
        directory = (root / bundle).resolve()
        if not maintenance_access_allowed() or directory.parent != root:
            return None, []
        manifest = directory / "maintenance-manifest.json"
        if not manifest.is_file():
            return None, []
        entries = json.loads(manifest.read_text(encoding="utf-8"))
        if not entries or any(Path(entry["name"]).name != entry["name"] or not entry["name"].endswith(".pdf") or not (directory / entry["name"]).is_file() for entry in entries):
            return None, []
        # Existing demo manifests listed the service sheet first, without delivery metadata.
        for index, entry in enumerate(entries):
            entry.setdefault("delivery", "customer_email" if index == 0 else "archive_only")
        return directory, entries

    @app.route("/demo/maintenance/<bundle>")
    def maintenance_bundle_page(bundle):
        directory, entries = demo_bundle(bundle)
        if directory is None:
            return json_error("Conjunto de demonstração não encontrado.", 404)
        return render_template("maintenance_bundle.html", bundle=bundle, entries=entries)

    @app.route("/demo/maintenance/<bundle>/pdf/<filename>")
    def maintenance_bundle_pdf(bundle, filename):
        directory, entries = demo_bundle(bundle)
        if directory is None or filename not in {entry["name"] for entry in entries}:
            return json_error("PDF não encontrado.", 404)
        return send_from_directory(directory, filename, as_attachment=request.args.get("download") == "1")

    @app.route("/demo/maintenance/<bundle>/simulate", methods=["POST"])
    def maintenance_bundle_simulate(bundle):
        directory, entries = demo_bundle(bundle)
        if directory is None:
            return json_error("Conjunto incompleto ou inexistente.", 404)
        attachments = [entry["name"] for entry in entries if entry.get("delivery") == "customer_email"]
        stored_only = [entry["name"] for entry in entries if entry.get("delivery") == "archive_only"]
        return jsonify(success=True, simulated=True, attachments=attachments, stored_only=stored_only,
                       message="Simulação concluída: a folha de serviço seria enviada ao cliente; "
                               "as checklists ficam guardadas na pasta do serviço. Nenhuma comunicação foi enviada.")

    def private_bundle(key):
        if not maintenance_access_allowed() or app.config["MAINTENANCE_DEMO"]:
            return None, [], None
        try:
            metadata, entries, directory = private_maintenance.bundle(key)
            archived_name = str(metadata["archived_name"])
            if (Path(archived_name).name != archived_name
                    or Path(archived_name).suffix.lower() not in {".xlsx", ".xlsm"}):
                return None, [], None
            from src.services import archive_service as archive_module
            archived = archive_module.EXCEL_ARQUIVADAS_DIR / Path(archived_name).stem / archived_name
            if not finalization_service.owns(archived, str(metadata["operation_id"])):
                return None, [], None
            return directory, entries, archived_name
        except (OSError, KeyError, ValueError, TypeError):
            return None, [], None

    @app.route("/maintenance/bundles/<key>")
    def private_maintenance_bundle_page(key):
        directory, entries, archived_name = private_bundle(key)
        if directory is None:
            return json_error("Conjunto SADI não encontrado.", 404)
        return render_template("maintenance_private_bundle.html", key=key, entries=entries,
                               archived_name=archived_name)

    @app.route("/maintenance/bundles/<key>/pdf/<filename>")
    def private_maintenance_bundle_pdf(key, filename):
        directory, entries, _ = private_bundle(key)
        if directory is None or filename not in {entry["name"] for entry in entries}:
            return json_error("PDF não encontrado.", 404)
        return send_from_directory(directory, filename, as_attachment=request.args.get("download") == "1")

    def load_editor_state(path):
        excel_form_data = serialize_payload(ExcelService(path).read_link_as_form_data())
        extra_data = DocumentDataService(path).read()
        if app.config["MAINTENANCE_ENABLED"] and not app.config["MAINTENANCE_DEMO"]:
            extra_data = dict(extra_data)
            extra_data["maintenance_checklists"] = trusted_maintenance(path)
        document_data = serialize_payload(
            normalize_document_for_file(
                document_from_excel_and_extra(
                    excel_form_data,
                    extra_data,
                ),
                path,
            )
        )
        canonical_number = FileService.service_number_from_name(path)
        if canonical_number:
            excel_form_data["Folha nº"] = canonical_number
        if not document_data.get("work_number"):
            log_event(
                app.logger,
                logging.WARNING,
                "Número de obra ausente ou inválido; ligação SharePoint desativada.",
                event="work_number_missing",
            )

        signatures = SignatureService(path).read_as_data_urls()
        return excel_form_data, document_data, signatures

    def schedule_graph_refresh(*, force: bool = False) -> dict[str, object] | None:
        if graph_refresh_coordinator is None:
            return None
        return graph_refresh_coordinator.request_refresh(force=force)

    def build_document_html(
        *,
        file_name: str,
        path,
        payload: dict[str, object],
        auto_print: bool = False,
    ) -> str:
        document_data = normalize_document_for_file(payload, file_name)
        responsible_technician = get_primary_technician_name(document_data)
        embedded_styles, logo_src = load_document_assets(app.static_folder, page_context=f"FS {document_data['service_number']}")
        return render_template(
            "service_document.html",
            file_name=file_name,
            document=document_data,
            signatures=SignatureService(path).resolve_from_form_data(payload),
            responsible_technician=responsible_technician,
            responsible_technician_initials=get_technician_initials(responsible_technician),
            service_type_options=SERVICE_TYPE_OPTIONS,
            equipment_options=EQUIPMENT_OPTIONS,
            auto_print=auto_print,
            embedded_styles=embedded_styles,
            logo_src=logo_src,
            asset_version=app.config["ASSET_VERSION"],
        )

    @app.route("/")
    def index():
        schedule_graph_refresh()
        selected_file_name = request.args.get("file", "").strip() or None
        selected_file_data = None
        selected_document_data = None
        selected_signatures = {}
        selected_file_error = None
        selected_editing_state = None
        selected_file_is_draft = None
        editor_identity = current_editor_identity()

        if selected_file_name:
            path = file_service.get_file_by_name(selected_file_name)
            if not path:
                selected_file_error = "Ficheiro não encontrado."
            else:
                # Sentinel only. The browser hydrates once from the bootstrap API.
                selected_document_data = {}
                selected_file_is_draft = file_service.is_draft_file(path)

        return render_template(
            "field_app.html",
            files=file_service.list_valid_files(),
            fields_by_group=FIELDS_BY_GROUP,
            field_map=FIELD_MAP,
            selected_file_name=selected_file_name,
            selected_file_data=selected_file_data,
            selected_document_data=selected_document_data,
            selected_signatures=selected_signatures,
            selected_file_error=selected_file_error,
            selected_editing_state=selected_editing_state,
            selected_file_is_draft=selected_file_is_draft,
            service_type_options=SERVICE_TYPE_OPTIONS,
            equipment_options=EQUIPMENT_OPTIONS,
            technician_options=TECHNICIAN_OPTIONS,
            document_required_fields=DOCUMENT_REQUIRED_FIELDS,
            technician_required_fields=TECHNICIAN_REQUIRED_FIELDS,
            signature_required_fields=SIGNATURE_REQUIRED_FIELDS,
            mail_enabled=mail_service is not None,
            graph_enabled=graph_service is not None,
            mail_test_recipient=(
                mail_service.config.test_recipient if mail_service is not None else ""
            ),
            mail_job_stale_seconds=(
                getattr(graph_sync_queue, "mail_job_stale_seconds", 180)
            ),
            teams_enabled=teams_notification_service is not None,
            current_user=g.current_user,
            editor_user={
                "id": editor_identity.id,
                "display_name": editor_identity.display_name,
            },
            asset_version=app.config["ASSET_VERSION"],
        )

    def render_work_folder_error(status_code: int, title: str, message: str):
        return render_template(
            "work_folder_error.html",
            status_code=status_code,
            title=title,
            message=message,
            asset_version=app.config["ASSET_VERSION"],
        ), status_code

    @app.get("/work-folder/<work_number>")
    def open_work_folder(work_number: str):
        if not re.fullmatch(r"[0-9]{4}", work_number):
            return render_work_folder_error(
                400,
                "Número de obra inválido",
                "O número de obra tem de conter exatamente quatro algarismos.",
            )

        try:
            folder = work_folder_service.resolve(work_number)
        except WorkFolderInvalidNumberError:
            return render_work_folder_error(
                400,
                "Número de obra inválido",
                "O número de obra tem de conter exatamente quatro algarismos.",
            )
        except WorkFolderNotFoundError:
            log_event(
                app.logger,
                logging.WARNING,
                "Pasta da obra não encontrada.",
                event="work_folder_not_found",
                work_number=work_number,
            )
            return render_work_folder_error(
                404,
                "Pasta de obra não encontrada",
                (
                    f"Não foi encontrada uma pasta com o número {work_number} em "
                    f"{GRAPH_WORKS_PATH.replace('/', ' / ')}."
                ),
            )
        except WorkFolderAmbiguousError:
            log_event(
                app.logger,
                logging.ERROR,
                "Número de obra duplicado no SharePoint.",
                event="work_folder_ambiguous",
                work_number=work_number,
            )
            return render_work_folder_error(
                409,
                "Número de obra duplicado",
                (
                    f"Existe mais de uma pasta para a obra {work_number}. "
                    "A ligação foi bloqueada para evitar abrir a pasta errada."
                ),
            )
        except WorkFolderUnsafeUrlError:
            log_event(
                app.logger,
                logging.ERROR,
                "Destino SharePoint inseguro rejeitado.",
                event="work_folder_unsafe_url",
                work_number=work_number,
            )
            return render_work_folder_error(
                502,
                "Destino SharePoint inválido",
                "O SharePoint devolveu um destino que a aplicação não pode abrir em segurança.",
            )
        except GraphStorageError:
            log_event(
                app.logger,
                logging.ERROR,
                "Falha Graph ao resolver a pasta da obra.",
                event="work_folder_graph_failed",
                work_number=work_number,
                exc_info=True,
            )
            return render_work_folder_error(
                502,
                "SharePoint temporariamente indisponível",
                "Não foi possível consultar as pastas de obra. Tente novamente dentro de momentos.",
            )

        return redirect(folder.web_url, code=302)

    @app.route("/manifest.webmanifest")
    def manifest():
        return send_from_directory(app.static_folder, "manifest.webmanifest")

    @app.route("/service-worker.js")
    def service_worker():
        return send_from_directory(app.static_folder, "service-worker.js")

    @app.route("/api/files")
    def get_files():
        refresh_mode = request.args.get("refresh")
        requested_refresh = None
        if refresh_mode != "0":
            requested_refresh = schedule_graph_refresh(force=refresh_mode in {"1", "true", "yes"})
        entries = file_service.list_valid_files()
        files_data = [serialize_file_entry(entry) for entry in entries]
        return jsonify({
            "success": True,
            "files": files_data,
            "html": render_template("partials/active_file_list.html", files=entries),
            "requested_refresh_id": (requested_refresh or {}).get("refresh_id"),
            "requested_refresh_sequence": (requested_refresh or {}).get("refresh_sequence"),
            "requested_refresh_generation": (requested_refresh or {}).get("generation_id"),
            "refresh": graph_refresh_coordinator.status() if graph_refresh_coordinator else None,
        })

    @app.route("/api/graph/status")
    def graph_status():
        if graph_service is None:
            return jsonify({
                "success": True,
                "backend": STORAGE_BACKEND,
                "graph_enabled": False,
                "refresh": None,
                "outbox": graph_sync_queue.summary() if graph_sync_queue else None,
                "mail": mail_service.status() if mail_service else {"enabled": False},
                "teams": (
                    teams_notification_service.status()
                    if teams_notification_service else {"enabled": False}
                ),
            })

        status = graph_service.status()
        return jsonify({
            "success": True,
            "backend": STORAGE_BACKEND,
            "graph_enabled": True,
            "graph": status,
            "refresh": graph_refresh_coordinator.status() if graph_refresh_coordinator else None,
            "outbox": graph_sync_queue.summary() if graph_sync_queue else None,
            "mail": mail_service.status() if mail_service else {"enabled": False},
            "teams": (
                teams_notification_service.status()
                if teams_notification_service else {"enabled": False}
            ),
        })

    @app.route("/api/graph/test")
    def graph_test():
        if graph_service is None:
            return json_error("Backend Graph não está ativo.", 400)

        try:
            return jsonify({
                "success": True,
                "graph": graph_service.test_connection(),
            })
        except GraphStorageError as exc:
            log_event(
                app.logger,
                logging.ERROR,
                "Diagnóstico Graph falhou.",
                event="graph_diagnostic_failed",
                exc_info=True,
            )
            return json_error(str(exc), 502)

    @app.route("/api/mail/test")
    def mail_test():
        if mail_service is None:
            return json_error("O envio de e-mail não está ativo.", 400)
        try:
            return jsonify({
                "success": True,
                "mail": mail_service.test_connection(),
            })
        except GraphMailConfigurationError as exc:
            log_event(
                app.logger,
                logging.ERROR,
                "Diagnóstico de e-mail encontrou configuração inválida.",
                event="mail_diagnostic_configuration_failed",
                error_type=type(exc).__name__,
            )
            return json_error(str(exc), 503)
        except GraphMailError as exc:
            log_event(
                app.logger,
                logging.ERROR,
                "Diagnóstico de e-mail falhou.",
                event="mail_diagnostic_failed",
                exc_info=True,
            )
            return json_error(str(exc), 502)

    @app.route("/api/graph/sync", methods=["POST"])
    def graph_sync():
        if graph_refresh_coordinator is None:
            return json_error("Backend Graph não está ativo.", 400)
        status = schedule_graph_refresh(force=True)
        return jsonify({"success": True, "refresh": status}), 202

    @app.route("/api/graph/jobs/<job_id>")
    def graph_job_status(job_id: str):
        if graph_sync_queue is None:
            return json_error("Backend Graph não está ativo.", 400)
        job = graph_sync_queue.status(job_id)
        if job is None:
            return json_error("Operação de sincronização não encontrada.", 404)
        return jsonify({"success": True, "job": job})

    @app.route("/api/graph/jobs/<job_id>/retry", methods=["POST"])
    def retry_graph_job(job_id: str):
        if graph_sync_queue is None:
            return json_error("A fila de operações não está ativa.", 400)
        try:
            job = graph_sync_queue.retry(job_id)
        except KeyError:
            return json_error("Operação de sincronização não encontrada.", 404)
        except ValueError as exc:
            return json_error(str(exc), 409)
        return jsonify({"success": True, "job": job}), 202

    @app.route("/api/file/<name>/bootstrap")
    def bootstrap_file_editor(name: str):
        path = file_service.get_file_by_name(name)
        if not path:
            refresh = graph_refresh_coordinator.status() if graph_refresh_coordinator else {}
            diagnostic("editor_file_not_found", level=logging.WARNING,
                       **lookup_fields(file_service, name),
                       refresh_in_progress=refresh.get("in_progress"),
                       last_refresh_started_at=refresh.get("last_started_at"),
                       last_refresh_completed_at=refresh.get("last_completed_at"),
                       last_refresh_had_error=bool(refresh.get("last_error")))
            return json_error("Ficheiro não encontrado", 404)
        client_id = str(request.args.get("client_id") or "").strip()
        if not client_id:
            return json_error("Identificador da sessão de edição em falta.", 400)
        try:
            form_data, source_document, signatures = load_editor_state(path)
            diagnostic("editor_file_loaded", **file_fields(name))
            identity = current_editor_identity()
            if file_service.is_draft_file(path):
                editing = editing_state_service.acquire_lease(
                    path, identity, client_id,
                    allow_foreign_owner=can_edit_foreign_sadi_draft(path),
                )
            else:
                editing = editing_state_service.acquire_private_workspace(
                    path, identity, client_id
                )
            server_document = editing.get("server_document")
            if isinstance(server_document, dict):
                server_document = normalize_document_for_file(server_document, path)
                editing = {**editing, "server_document": server_document}
            effective_document = server_document or source_document
            return jsonify({
                "success": True,
                "file": name,
                "is_draft": file_service.is_draft_file(path),
                "data": form_data,
                "source_document": source_document,
                "document": effective_document,
                "signatures": signatures,
                "photos": photos_for_response(path),
                "recovery_source": "server" if editing.get("server_document") else "source",
                "editing": editing,
            })
        except LeaseConflictError as exc:
            return jsonify({
                "success": False,
                "editable": False,
                "error": str(exc),
                "code": "lease_conflict",
                "file": name,
                "is_draft": True,
                "data": form_data,
                "source_document": source_document,
                "document": source_document,
                "signatures": signatures,
                "photos": photos_for_response(path),
                "editing": exc.snapshot,
            }), 423
        except EditingStateError as exc:
            return editing_error_response(exc)
        except ExcelValidationError as exc:
            return json_error(str(exc), 400)
        except Exception as exc:
            log_event(
                app.logger,
                logging.ERROR,
                "Falha inesperada ao inicializar o editor.",
                event="editor_bootstrap_failed",
                exc_info=True,
            )
            return json_error(str(exc), 500)

    @app.route("/api/file/<name>")
    def get_file_data(name: str):
        path = file_service.get_file_by_name(name)
        if not path:
            return json_error("Ficheiro não encontrado", 404)

        try:
            form_data, document_data, signatures = load_editor_state(path)
            editing = editing_state_service.snapshot(path)
            server_document = editing.get("server_document")
            if isinstance(server_document, dict):
                editing = {
                    **editing,
                    "server_document": normalize_document_for_file(server_document, path),
                }
            if not file_service.is_draft_file(path):
                editing = {
                    **editing,
                    "lease": None,
                    "server_document": None,
                    "autosaved_at": None,
                }
            return jsonify({
                "success": True,
                "file": name,
                "data": form_data,
                "document": document_data,
                "signatures": signatures,
                "photos": photos_for_response(path),
                "editing": editing,
            })
        except ExcelValidationError as exc:
            return json_error(str(exc), 400)
        except Exception as exc:
            log_event(
                app.logger,
                logging.ERROR,
                "Falha inesperada ao ler a folha.",
                event="file_read_failed",
                exc_info=True,
            )
            return json_error(str(exc), 500)
    @app.route("/api/file/<name>/photos/<photo_id>")
    def get_file_photo(name: str, photo_id: str):
        path = file_service.get_file_by_name(name)
        if not path:
            return json_error("Ficheiro não encontrado", 404)
        photo_path = PhotoAttachmentService(path).resolve(photo_id)
        if photo_path is None:
            return json_error("Fotografia não encontrada", 404)
        return send_from_directory(
            photo_path.parent,
            photo_path.name,
            mimetype=PhotoAttachmentService.content_type_for_path(photo_path),
            conditional=True,
            max_age=0,
        )


    @app.route("/api/file/<name>/lease", methods=["POST"])
    def acquire_file_lease(name: str):
        path = file_service.get_file_by_name(name)
        if not path:
            return json_error("Ficheiro não encontrado", 404)
        data = request.get_json(silent=True) or {}
        client_id = str(data.get("client_id") or "").strip()
        try:
            identity = current_editor_identity()
            if file_service.is_draft_file(path):
                editing = editing_state_service.acquire_lease(
                    path, identity, client_id,
                    allow_foreign_owner=can_edit_foreign_sadi_draft(path),
                )
            else:
                editing = editing_state_service.acquire_private_workspace(
                    path,
                    identity,
                    client_id,
                )
            return jsonify({"success": True, "editable": True, "editing": editing})
        except EditingStateError as exc:
            return editing_error_response(exc)

    @app.route("/api/file/<name>/lease/heartbeat", methods=["POST"])
    def heartbeat_file_lease(name: str):
        path = file_service.get_file_by_name(name)
        if not path:
            return json_error("Ficheiro não encontrado", 404)
        data = request.get_json(silent=True) or {}
        try:
            document_id = str(data.get("document_id") or "")
            expected_id = resolve_editing_document_id(
                path, str(data.get("client_id") or "")
            )
            if document_id != expected_id:
                raise RevisionConflictError(editing_state_service.snapshot_document(expected_id))
            editing = editing_state_service.renew_lease(
                document_id,
                current_editor_identity(),
                str(data.get("client_id") or ""),
                str(data.get("lease_token") or ""),
            )
            return jsonify({"success": True, "editing": editing})
        except EditingStateError as exc:
            return editing_error_response(exc)

    @app.route("/api/file/<name>/lease/release", methods=["POST"])
    def release_file_lease(name: str):
        data = request.get_json(silent=True) or {}
        document_id = str(data.get("document_id") or "")
        if not document_id:
            return json_error("Documento em falta.", 400)
        try:
            released = editing_state_service.release_lease(
                document_id,
                current_editor_identity(),
                str(data.get("client_id") or ""),
                str(data.get("lease_token") or ""),
            )
            return jsonify({"success": True, "released": released})
        except EditingStateError as exc:
            return editing_error_response(exc)

    @app.route("/api/file/<name>/editing/close", methods=["POST"])
    def close_file_editing(name: str):
        path = file_service.get_file_by_name(name)
        if not path:
            return json_error("Ficheiro não encontrado", 404)
        data = request.get_json(silent=True) or {}
        document_payload = data.get("document")
        if document_payload is not None and not isinstance(document_payload, dict):
            return json_error("Documento de autosave inválido.", 400)
        try:
            metadata = extract_edit_metadata(data)
            document_id = validate_document_identity(path, metadata)
            document = (
                strip_signature_payload(normalize_document_for_write(document_payload, path))
                if isinstance(document_payload, dict)
                else None
            )
            result = editing_state_service.close_editing_session(
                document_id=document_id,
                identity=current_editor_identity(),
                client_id=str(metadata["client_id"]),
                lease_token=str(metadata["lease_token"]),
                base_revision=int(metadata["base_revision"]),
                idempotency_key=str(
                    metadata.get("idempotency_key") or secrets.token_urlsafe(18)
                ),
                document=document,
            )
            return jsonify({"success": True, **result})
        except EditingStateError as exc:
            return editing_error_response(exc)

    @app.route("/api/file/<name>/autosave", methods=["POST"])
    def autosave_file(name: str):
        path = file_service.get_file_by_name(name)
        if not path:
            return json_error("Ficheiro não encontrado", 404)
        data = request.get_json(silent=True) or {}
        document_payload = data.get("document")
        if not isinstance(document_payload, dict):
            return json_error("Documento de autosave inválido.", 400)
        try:
            metadata = extract_edit_metadata(data)
            document_id = validate_document_identity(path, metadata)
            idempotency_key = str(metadata.get("idempotency_key") or secrets.token_urlsafe(18))
            document = strip_signature_payload(
                normalize_document_for_write(document_payload, path)
            )
            editing = editing_state_service.save_autosave(
                document_id=document_id,
                identity=current_editor_identity(),
                client_id=str(metadata["client_id"]),
                lease_token=str(metadata["lease_token"]),
                base_revision=int(metadata["base_revision"]),
                idempotency_key=idempotency_key,
                document=document,
            )
            return jsonify({
                "success": True,
                "message": "Alterações guardadas no servidor.",
                "editing": editing,
            })
        except EditingStateError as exc:
            return editing_error_response(exc)

    @app.route("/api/file/<name>/autosave/discard", methods=["POST"])
    def discard_file_autosave(name: str):
        path = file_service.get_file_by_name(name)
        if not path:
            return json_error("Ficheiro não encontrado", 404)
        data = request.get_json(silent=True) or {}
        try:
            metadata = extract_edit_metadata(data)
            document_id = validate_document_identity(path, metadata)
            editing = editing_state_service.discard_autosave(
                document_id=document_id,
                identity=current_editor_identity(),
                client_id=str(metadata["client_id"]),
                lease_token=str(metadata["lease_token"]),
                base_revision=int(metadata["base_revision"]),
            )
            return jsonify({"success": True, "editing": editing})
        except EditingStateError as exc:
            return editing_error_response(exc)

    @app.route("/api/file/<name>/document-preview", methods=["POST"])
    def preview_document(name: str):
        path = file_service.get_file_by_name(name)
        if not path:
            return json_error("Ficheiro não encontrado", 404)

        payload = request.get_json(silent=True)
        if not payload:
            return json_error("Sem dados", 400)

        try:
            selected_site = None
            document = normalize_document_for_file(payload, path)
            if "_maintenance_site_id" in payload:
                if not maintenance_access_allowed():
                    return json_error("Checklist não disponível.", 404)
                if not file_service.is_draft_file(path):
                    return json_error("Crie primeiro um rascunho.", 409)
                if not maintenance.applicable(document):
                    return json_error("Selecione Manutenção e SADI.", 400)
                sites = [site for site in document["maintenance_checklists"]
                         if site["id"] == payload["_maintenance_site_id"]]
                if len(sites) != 1:
                    return json_error("Selecione um local válido para pré-visualizar a checklist.", 400)
                selected_site = sites[0]
                if payload.get("_auto_print"):
                    return jsonify(success=True, html=render_maintenance_document(
                        document, selected_site, draft_preview=True, auto_print=True))
            html = build_document_html(
                file_name=name,
                path=path,
                payload=payload,
                auto_print=bool(payload.get("_auto_print")),
            )
            if (not payload.get("_auto_print") and maintenance_access_allowed()
                    and file_service.is_draft_file(path) and maintenance.applicable(document)):
                documents = [{"id": "service", "label": "Folha de serviço", "html": html}]
                selected = "service"
                for index, site in enumerate(document["maintenance_checklists"]):
                    key = f"sadi-{index}"
                    documents.append({"id": key, "label": f"SADI — {site['location'] or f'Local {index + 1}'}",
                                      "html": render_maintenance_document(document, site, draft_preview=True)})
                    if site is selected_site:
                        selected = key
                html = render_template("document_preview.html",
                                       preview={"documents": documents, "selected": selected})
            return jsonify({"success": True, "html": html})
        except Exception as exc:
            log_event(
                app.logger,
                logging.ERROR,
                "Falha inesperada ao gerar a pré-visualização.",
                event="document_preview_failed",
                exc_info=True,
            )
            return json_error(str(exc), 500)

    @app.route("/api/file/<name>/draft", methods=["POST"])
    @exclusive_operation
    def save_draft(name: str):
        path = file_service.get_file_by_name(name)
        if not path:
            return json_error("Ficheiro não encontrado", 404)

        document_id = ""
        idempotency_key = ""
        try:
            received, photo_uploads, removed_photo_ids = parse_commit_request()
            prepared_photos = PhotoAttachmentService.prepare_uploads(photo_uploads)
            PhotoAttachmentService(path).validate_changes(
                prepared_photos,
                removed_photo_ids,
            )
            data = dict(received)
            metadata = extract_edit_metadata(data, require_idempotency=True)
            document_id = validate_document_identity(path, metadata)
            idempotency_key = str(metadata["idempotency_key"])
            claim = editing_state_service.claim_operation(
                document_id=document_id,
                kind="draft",
                idempotency_key=idempotency_key,
                identity=current_editor_identity(),
                client_id=str(metadata["client_id"]),
                lease_token=str(metadata["lease_token"]),
                base_revision=int(metadata["base_revision"]),
            )
            if claim["status"] == "replay":
                return jsonify(claim["operation"]["response"])

            context = dict(claim["operation"].get("context") or {})
            draft_path = Path(str(context.get("draft_path") or path))
            created_copy = bool(context.get("created_copy"))
            created_this_attempt = False

            if not context.get("draft_path") and not file_service.is_draft_file(path):
                draft_path = file_service.create_draft_copy(
                    path,
                    get_primary_technician_name(data),
                )
                created_copy = draft_path != path
                created_this_attempt = created_copy
                editing_state_service.update_operation_context(
                    document_id,
                    idempotency_key,
                    draft_path=str(draft_path),
                    created_copy=created_copy,
                )

            document_data = normalize_document_for_write(data, draft_path)
            if document_data.get("client_not_present"):
                data[CLIENT_SIGNATURE_LABEL] = ""
            excel_form_data = document_to_excel_form(document_data)
            signature_service = SignatureService(draft_path)
            resolved_signatures = signature_service.resolve_from_form_data(data)
            if graph_service is not None:
                mark_dirty(draft_path.parent)
            ExcelService(draft_path).write_link_from_form(
                excel_form_data,
                signatures=resolved_signatures,
                trusted_service_number=str(document_data.get("service_number") or ""),
            )
            DocumentDataService(draft_path).write(strip_signature_payload(public_document(document_data)))
            if app.config["MAINTENANCE_ENABLED"] and not app.config["MAINTENANCE_DEMO"] and maintenance_access_allowed():
                private_maintenance.write(
                    editing_state_service.resolve_document_id(draft_path),
                    document_data["maintenance_checklists"],
                )
            signature_service.save_from_form_data(data)
            PhotoAttachmentService(draft_path).apply(
                prepared_photos,
                removed_photo_ids,
            )

            result_editing = None
            if created_copy:
                draft_document_id = editing_state_service.resolve_document_id(draft_path)
                editing_state_service.associate_path(
                    draft_path,
                    draft_document_id,
                    original_name=path.stem,
                    owner=current_editor_identity(),
                )
                result_editing = editing_state_service.snapshot(draft_path, draft_document_id)

            graph_uploaded_files = []
            graph_sync_job = None
            if graph_service is not None and graph_sync_queue is not None:
                graph_sync_job = graph_sync_queue.enqueue(
                    "upload_active",
                    {
                        "draft_path": str(draft_path),
                        "fail_if_exists": created_this_attempt,
                    },
                    job_id=f"draft:{document_id}:{idempotency_key}",
                )
            file_service.invalidate_cache(draft_path)
            result = editing_state_service.commit_operation(
                document_id=document_id,
                idempotency_key=idempotency_key,
                path=None if created_copy else draft_path,
                release_lease=created_copy,
                result_editing=result_editing,
                response={
                    "success": True,
                    "message": (
                        "Rascunho guardado no servidor; publicação no SharePoint pendente."
                        if graph_sync_job else "Rascunho guardado."
                    ),
                    "file": draft_path.stem,
                    "status": "in_progress",
                    "created_copy": created_copy,
                    "photos": photos_for_response(draft_path),
                    "graph_uploaded_files": graph_uploaded_files,
                    "graph_job_id": graph_sync_job.get("id") if graph_sync_job else None,
                    "publication_status": (
                        "pending" if graph_service is not None and graph_sync_job else "local"
                    ),
                },
            )
            log_event(
                app.logger,
                logging.INFO,
                "Rascunho guardado.",
                event="draft_saved",
                document_id=document_id,
                created_copy=created_copy,
                publication_queued=graph_sync_job is not None,
            )
            return jsonify(result)
        except PhotoAttachmentError as exc:
            if document_id and idempotency_key:
                editing_state_service.fail_operation(document_id, idempotency_key, str(exc))
            return json_error(str(exc), 400)
        except EditingStateError as exc:
            return editing_error_response(exc)
        except GraphConflictError as exc:
            if document_id and idempotency_key:
                editing_state_service.fail_operation(document_id, idempotency_key, str(exc))
            return jsonify({
                "success": False,
                "error": str(exc),
                "code": "graph_conflict",
                "editing": editing_state_service.snapshot_document(document_id) if document_id else None,
            }), 409
        except ExcelValidationError as exc:
            if document_id and idempotency_key:
                editing_state_service.fail_operation(document_id, idempotency_key, str(exc))
            return json_error(str(exc), 400)
        except Exception as exc:
            if document_id and idempotency_key:
                editing_state_service.fail_operation(document_id, idempotency_key, str(exc))
            log_event(
                app.logger,
                logging.ERROR,
                "Falha inesperada ao guardar o rascunho.",
                event="draft_save_failed",
                document_id=document_id or None,
                exc_info=True,
            )
            return json_error(str(exc), 500)

    @app.route("/api/file/<name>/send", methods=["POST"])
    @exclusive_operation
    def save_and_send(name: str):
        document_id = ""
        idempotency_key = ""
        try:
            received, photo_uploads, removed_photo_ids = parse_commit_request()
            document_payload = dict(received)
            metadata = extract_edit_metadata(document_payload, require_idempotency=True)
            document_id = str(metadata["document_id"])
            idempotency_key = str(metadata["idempotency_key"])
            existing_operation = editing_state_service.operation(document_id, idempotency_key)
            if existing_operation and existing_operation.get("status") == "complete":
                return jsonify(existing_operation["response"])

            operation_context = dict((existing_operation or {}).get("context") or {})
            if operation_context.get("prepared_document"):
                document_payload = dict(operation_context["prepared_document"])
            path = file_service.get_file_by_name(name)
            archived_value = str(operation_context.get("archived_path") or "")
            archived_path = Path(archived_value) if archived_value else None
            if archived_path is not None and not archived_path.exists():
                archived_path = None
            if path is None and archived_path is None:
                return json_error("Ficheiro não encontrado", 404)
            if path is not None and not file_service.is_draft_file(path):
                return jsonify({
                    "success": False,
                    "error": "Crie primeiro um rascunho individual antes de finalizar a folha.",
                    "code": "draft_required",
                }), 409
            if path is not None:
                validate_document_identity(path, metadata)

            if path is not None and graph_service is not None and graph_sync_queue is not None and graph_sync_queue.has_unfinished_upload(path):
                return jsonify({"success": False, "code": "publication_pending",
                    "error": "A publicação do rascunho ainda não terminou. Aguarde a sincronização antes de finalizar."}), 409

            internal_observations = pop_internal_observations(document_payload)
            document_data = normalize_document_for_write(
                document_payload,
                archived_path or path or name,
            )
            service_number = str(document_data.get("service_number") or name)
            missing_fields = document_missing_required_fields(document_data)
            invalid_fields = document_invalid_fields(document_data)
            if app.config["MAINTENANCE_ENABLED"]:
                checklist_errors = maintenance.document_errors(document_data, app.secret_key)
                if maintenance_access_allowed():
                    missing_fields.extend(checklist_errors)
                elif checklist_errors:
                    missing_fields.append("Checklist de manutenção SADI pendente")
            elif document_data.get("maintenance_checklists"):
                return json_error("A finalização de checklists está disponível apenas na demo isolada.", 409)
            mail_payload = None
            teams_payload = None
            customer_email = str(document_data.get("customer_email") or "").strip()
            if mail_service is not None and customer_email:
                try:
                    mail_service.validate_address(
                        customer_email,
                        label="e-mail do cliente",
                    )
                except GraphMailError:
                    invalid_fields.append("E-mail do cliente")
            if document_data.get("client_not_present"):
                document_payload[CLIENT_SIGNATURE_LABEL] = ""
            validation_path = archived_path or path
            prepared_photos = PhotoAttachmentService.prepare_uploads(photo_uploads)
            PhotoAttachmentService(validation_path).validate_changes(
                prepared_photos,
                removed_photo_ids,
            )
            resolved_signatures = SignatureService(validation_path).resolve_from_form_data(
                document_payload
            )
            if not document_data.get("client_not_present"):
                missing_fields.extend(
                    label for label in SignatureService.required_labels()
                    if not resolved_signatures.get(label)
                )
            if missing_fields or invalid_fields:
                return jsonify({
                    "success": False,
                    "error": (
                        "Existem campos obrigatórios por preencher."
                        if missing_fields
                        else "Existem campos com valores inválidos."
                    ),
                    "missing_fields": missing_fields,
                    "invalid_fields": invalid_fields,
                }), 400

            if mail_service is not None and customer_email:
                if graph_sync_queue is None:
                    return json_error(
                        "O envio de e-mail requer o backend Microsoft Graph ativo.",
                        503,
                    )
                current_user = g.get("current_user")
                technician_email = str(getattr(current_user, "email", "") or "").strip()
                if not technician_email:
                    return json_error(
                        "Não foi possível determinar o e-mail do técnico autenticado.",
                        400,
                    )
                try:
                    mail_payload = mail_service.prepare_service_email(
                        customer_email=customer_email,
                        technician_email=technician_email,
                        customer_name=str(document_data.get("customer_name") or ""),
                        service_number=service_number,
                        document_language=str(document_data.get("document_language") or "pt"),
                    )
                except GraphMailConfigurationError as exc:
                    return json_error(str(exc), 503)
                except GraphMailError as exc:
                    return json_error(str(exc), 400)

                try:
                    mail_service.test_connection()
                except GraphMailConfigurationError as exc:
                    return json_error(str(exc), 503)
                except GraphMailError as exc:
                    return json_error(str(exc), 502)

                if teams_notification_service is not None:
                    try:
                        teams_payload = (
                            teams_notification_service.prepare_service_sent_notification(
                                service_number=service_number,
                            )
                        )
                    except TeamsNotificationConfigurationError as exc:
                        return json_error(str(exc), 503)
                    except TeamsNotificationError as exc:
                        return json_error(str(exc), 400)

            claim = editing_state_service.claim_operation(
                document_id=document_id,
                kind="send",
                idempotency_key=idempotency_key,
                identity=current_editor_identity(),
                client_id=str(metadata["client_id"]),
                lease_token=str(metadata["lease_token"]),
                base_revision=int(metadata["base_revision"]),
            )
            if claim["status"] == "replay":
                return jsonify(claim["operation"]["response"])
            operation_context = dict(claim["operation"].get("context") or {})
            private_bundle_key = str(operation_context.get("private_bundle_key") or "")

            source_path = Path(str(operation_context.get("source_path") or path))
            graph_active_etag = str(operation_context.get("graph_active_etag") or "") or None
            graph_active_source_name = str(operation_context.get("graph_active_source_name") or "")
            if archived_path is None:
                if graph_service is not None:
                    graph_active_source_name = graph_service.active_source_name(source_path)
                    graph_active_etag = graph_service.active_source_etag(source_path)
                    if not graph_active_etag:
                        raise GraphConflictError("O rascunho ainda não tem uma versão confirmada no SharePoint. Publique-o antes de finalizar.")
                staged_path, target_path = finalization_service.prepare(source_path, f"{document_id}:{idempotency_key}")
                try:
                    PhotoAttachmentService(staged_path).apply(prepared_photos, removed_photo_ids)
                    ExcelService(staged_path).write_link_from_form(
                        document_to_excel_form(document_data), signatures=resolved_signatures,
                        final=True, trusted_service_number=service_number,
                    )
                    DocumentDataService(staged_path).write(strip_signature_payload(public_document(document_data)))
                    SignatureService(staged_path).save_from_form_data(document_payload)
                    write_internal_observations(staged_path, internal_observations)
                    DocumentArtifactService(staged_path).write_html(build_document_html(
                        file_name=staged_path.stem, path=staged_path, payload=document_payload, auto_print=False,
                    ))
                    if app.config["MAINTENANCE_ENABLED"] and maintenance.applicable(document_data):
                        renderer = local_pdf_service or LocalPdfService()
                        if app.config["MAINTENANCE_DEMO"]:
                            build_maintenance_bundle(staged_path, document_data, renderer)
                        else:
                            private_bundle_key = private_maintenance.publish_bundle(
                                staged_path, document_data, renderer, document_id,
                                f"{document_id}:{idempotency_key}", target_path.name,
                            )
                    editing_state_service.update_operation_context(
                        document_id, idempotency_key, source_path=str(source_path),
                        archived_path=str(target_path), prepared_document=document_payload,
                        prepared_mail=mail_payload, prepared_teams=teams_payload,
                        source_fingerprint=editing_state_service._fingerprint(source_path),
                        graph_active_etag=graph_active_etag,
                        graph_active_source_name=graph_active_source_name,
                        private_bundle_key=private_bundle_key,
                    )
                    archived_path = finalization_service.publish(staged_path, target_path, f"{document_id}:{idempotency_key}")
                finally:
                    finalization_service.discard(staged_path)
                editing_state_service.update_operation_context(
                    document_id,
                    idempotency_key,
                    source_path=str(source_path),
                    archived_path=str(archived_path),
                    graph_active_etag=graph_active_etag,
                    graph_active_source_name=graph_active_source_name,
                )
            else:
                mail_payload = operation_context.get("prepared_mail", mail_payload)
                teams_payload = operation_context.get("prepared_teams", teams_payload)
            internal_observations_path = archived_path.with_name(f"{archived_path.stem}__observacoes_internas.txt")
            if not internal_observations_path.exists():
                internal_observations_path = None
            commit_guard = {"database": str(editing_state_service.repository.database_path.resolve()),
                "document_id": document_id, "operation_id": idempotency_key}
            graph_uploaded_files = []
            graph_removed_active = False
            graph_sync_job = None
            if graph_service is not None and graph_sync_queue is not None:
                graph_job_payload = {
                    "_commit_guard": commit_guard,
                    "archived_path": str(archived_path),
                    "source_path": str(source_path),
                    "source_name": graph_active_source_name,
                    "expected_etag": graph_active_etag,
                }
                if mail_payload is not None:
                    graph_job_payload["mail"] = mail_payload
                if teams_payload is not None:
                    graph_job_payload["teams"] = teams_payload
                graph_sync_job = graph_sync_queue.enqueue(
                    "archive_and_remove",
                    graph_job_payload,
                    job_id=f"send:{document_id}:{idempotency_key}",
                )
            elif mail_payload is not None and graph_sync_queue is not None:
                graph_sync_job = graph_sync_queue.enqueue(
                    "archive_and_mail_local",
                    {
                        "_commit_guard": commit_guard,
                        "archived_path": str(archived_path),
                        "mail": mail_payload,
                        "teams": teams_payload,
                    },
                    job_id=f"send:{document_id}:{idempotency_key}",
                )

            file_service.invalidate_cache(source_path)
            result = editing_state_service.commit_operation(
                document_id=document_id,
                idempotency_key=idempotency_key,
                path=archived_path,
                release_lease=True,
                response={
                    "success": True,
                    "message": (
                        (
                            "Folha arquivada; publicação do PDF, envio por e-mail e aviso no Teams pendentes."
                            if graph_service is not None and teams_payload is not None
                            else (
                                "Folha arquivada; geração do PDF, envio por e-mail e aviso no Teams pendentes."
                                if teams_payload is not None
                                else (
                                    "Folha arquivada; publicação do PDF e envio por e-mail pendentes."
                                    if graph_service is not None
                                    else "Folha arquivada; geração do PDF e envio por e-mail pendentes."
                                )
                            )
                        )
                        if graph_sync_job and mail_payload is not None
                        else (
                            "Folha finalizada no servidor; publicação no SharePoint pendente."
                            if graph_sync_job else "Folha finalizada com sucesso."
                        )
                    ),
                    "archived_excel": archived_path.name,
                    "maintenance_bundle_url": (
                        (
                            url_for("maintenance_bundle_page", bundle=archived_path.parent.name)
                            if app.config["MAINTENANCE_DEMO"] else
                            url_for("private_maintenance_bundle_page", key=private_bundle_key)
                        )
                        if maintenance_access_allowed() and maintenance.applicable(document_data)
                        and (app.config["MAINTENANCE_DEMO"] or private_bundle_key) else None
                    ),
                    "internal_observations": (
                        internal_observations_path.name if internal_observations_path else None
                    ),
                    "graph_uploaded_files": graph_uploaded_files,
                    "graph_removed_active": graph_removed_active,
                    "graph_job_id": graph_sync_job.get("id") if graph_sync_job else None,
                    "publication_status": (
                        "pending" if graph_service is not None and graph_sync_job else "local"
                    ),
                    "email_status": (
                        "pending" if mail_payload is not None else "disabled"
                    ),
                    "email_recipient": (
                        mail_payload.get("to") if mail_payload is not None else None
                    ),
                    "teams_status": (
                        "pending" if teams_payload is not None else "disabled"
                    ),
                },
            )
            context = editing_state_service.operation(document_id, idempotency_key)["context"]
            try:
                retire_committed_source(document_id, idempotency_key, context)
            except OSError:
                app.logger.exception("Arquivo completo; limpeza da origem será recuperada na próxima abertura.")
            log_event(
                app.logger,
                logging.INFO,
                "Folha finalizada.",
                event="document_finalized",
                document_id=document_id,
                publication_queued=graph_sync_job is not None,
                email_queued=mail_payload is not None,
                teams_queued=teams_payload is not None,
            )
            return jsonify(result)
        except PhotoAttachmentError as exc:
            if document_id and idempotency_key:
                editing_state_service.fail_operation(document_id, idempotency_key, str(exc))
            return json_error(str(exc), 400)
        except EditingStateError as exc:
            return editing_error_response(exc)
        except GraphConflictError as exc:
            if document_id and idempotency_key:
                editing_state_service.fail_operation(document_id, idempotency_key, str(exc))
            return jsonify({
                "success": False,
                "error": str(exc),
                "code": "graph_conflict",
                "editing": editing_state_service.snapshot_document(document_id) if document_id else None,
            }), 409
        except ExcelValidationError as exc:
            if document_id and idempotency_key:
                editing_state_service.fail_operation(document_id, idempotency_key, str(exc))
            return json_error(str(exc), 400)
        except Exception as exc:
            if document_id and idempotency_key:
                editing_state_service.fail_operation(document_id, idempotency_key, str(exc))
            log_event(
                app.logger,
                logging.ERROR,
                "Falha inesperada ao finalizar a folha.",
                event="document_send_failed",
                document_id=document_id or None,
                exc_info=True,
            )
            return json_error(str(exc), 500)

    @app.route("/api/file/<name>/cancel", methods=["POST"])
    @exclusive_operation
    def cancel_file(name: str):
        received = request.get_json(silent=True) or {}
        document_id = ""
        idempotency_key = ""
        try:
            data = dict(received)
            metadata = extract_edit_metadata(data, require_idempotency=True)
            document_id = str(metadata["document_id"])
            idempotency_key = str(metadata["idempotency_key"])
            existing_operation = editing_state_service.operation(document_id, idempotency_key)
            if existing_operation and existing_operation.get("status") == "complete":
                return jsonify(existing_operation["response"])
            context = dict((existing_operation or {}).get("context") or {})
            path = file_service.get_file_by_name(name)
            canceled_value = str(context.get("canceled_path") or "")
            canceled_path = Path(canceled_value) if canceled_value else None
            if canceled_path is not None and not canceled_path.exists():
                canceled_path = None
            if path is None and canceled_path is None:
                return json_error("Ficheiro não encontrado", 404)
            if path is not None and not file_service.is_draft_file(path):
                return jsonify({
                    "success": False,
                    "error": "A folha original não pode ser cancelada; crie primeiro um rascunho individual.",
                    "code": "draft_required",
                }), 409
            if path is not None:
                validate_document_identity(path, metadata)

            claim = editing_state_service.claim_operation(
                document_id=document_id,
                kind="cancel",
                idempotency_key=idempotency_key,
                identity=current_editor_identity(),
                client_id=str(metadata["client_id"]),
                lease_token=str(metadata["lease_token"]),
                base_revision=int(metadata["base_revision"]),
            )
            if claim["status"] == "replay":
                return jsonify(claim["operation"]["response"])
            context = dict(claim["operation"].get("context") or {})
            source_path = Path(str(context.get("source_path") or path))
            graph_active_etag = str(context.get("graph_active_etag") or "") or None
            graph_active_source_name = str(context.get("graph_active_source_name") or "")
            if canceled_path is None:
                if graph_service is not None:
                    graph_active_source_name = graph_service.active_source_name(source_path)
                    graph_active_etag = graph_service.active_source_etag(source_path)
                canceled_path = archive_service.cancel(source_path)
                editing_state_service.update_operation_context(
                    document_id,
                    idempotency_key,
                    source_path=str(source_path),
                    canceled_path=str(canceled_path),
                    graph_active_etag=graph_active_etag,
                    graph_active_source_name=graph_active_source_name,
                )
            graph_removed_active = False
            graph_sync_job = None
            if graph_service is not None and graph_sync_queue is not None:
                graph_sync_job = graph_sync_queue.enqueue(
                    "remove_active",
                    {
                        "source_path": str(source_path),
                        "expected_etag": graph_active_etag,
                        "source_name": graph_active_source_name,
                    },
                    job_id=f"cancel:{document_id}:{idempotency_key}",
                )
            file_service.invalidate_cache()
            result = editing_state_service.commit_operation(
                document_id=document_id,
                idempotency_key=idempotency_key,
                path=canceled_path,
                release_lease=True,
                response={
                    "success": True,
                    "message": (
                        "Folha cancelada no servidor; atualização do SharePoint pendente."
                        if graph_sync_job else "Folha cancelada com sucesso."
                    ),
                    "canceled_excel": canceled_path.name,
                    "graph_removed_active": graph_removed_active,
                    "graph_job_id": graph_sync_job.get("id") if graph_sync_job else None,
                    "publication_status": (
                        "pending" if graph_service is not None and graph_sync_job else "local"
                    ),
                },
            )
            log_event(
                app.logger,
                logging.INFO,
                "Folha cancelada.",
                event="document_canceled",
                document_id=document_id,
                publication_queued=graph_sync_job is not None,
            )
            return jsonify(result)
        except EditingStateError as exc:
            return editing_error_response(exc)
        except GraphConflictError as exc:
            if document_id and idempotency_key:
                editing_state_service.fail_operation(document_id, idempotency_key, str(exc))
            return jsonify({
                "success": False,
                "error": str(exc),
                "code": "graph_conflict",
                "editing": editing_state_service.snapshot_document(document_id) if document_id else None,
            }), 409
        except Exception as exc:
            if document_id and idempotency_key:
                editing_state_service.fail_operation(document_id, idempotency_key, str(exc))
            log_event(
                app.logger,
                logging.ERROR,
                "Falha inesperada ao cancelar a folha.",
                event="document_cancel_failed",
                document_id=document_id or None,
                exc_info=True,
            )
            return json_error(str(exc), 500)

    return app


app = create_app()
