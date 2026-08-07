"""Aplicação Flask e API da webapp de Folhas de Serviço."""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
import re
import secrets
import traceback
from pathlib import Path

from flask import Flask, g, jsonify, redirect, render_template, request, send_from_directory, session, url_for

from src.config import (
    APP_DATA_DIR,
    AUTH_PROVIDER,
    GRAPH_SHAREPOINT_HOSTNAME,
    GRAPH_WORKS_CACHE_SECONDS,
    GRAPH_WORKS_PATH,
    MAIL_ENABLED,
    MICROSOFT_AUTH_REDIRECT_URI,
    TEAMS_NOTIFICATIONS_ENABLED,
    STORAGE_BACKEND,
)
from src.document_schema import (
    DOCUMENT_REQUIRED_FIELDS,
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
from src.services.archive_service import ArchiveService
from src.services.document_artifact_service import DocumentArtifactService
from src.services.document_data_service import DocumentDataService
from src.services.editing_state_service import (
    EditingStateError,
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


def _load_secret_key() -> str:
    return os.environ.get("FS_SECRET_KEY", "dev-local-secret")


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
) -> Flask:
    """Cria a aplicação Flask com dependências injetáveis para testes."""
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["ASSET_VERSION"] = dt.datetime.now().strftime("%Y%m%d%H%M%S")
    app.secret_key = _load_secret_key()
    app.config.update(
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
        )
        if graph_service is not None or mail_service is not None else None
    )

    @app.after_request
    def apply_cache_headers(response):
        if request.path.startswith(("/api/", "/work-folder/")) or request.path in {
            "/",
            "/login",
            "/service-worker.js",
            "/static/js/field-app.js",
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

    @app.errorhandler(413)
    def request_too_large(_error):
        return json_error("O pedido excede o limite máximo permitido.", 413)

    def current_editor_identity() -> EditorIdentity:
        current_user = g.get("current_user")
        if current_user is not None:
            return EditorIdentity(
                id=str(current_user.id),
                display_name=str(current_user.display_name),
            )

        anonymous_id = str(session.get("anonymous_editor_id") or "")
        if not anonymous_id:
            anonymous_id = secrets.token_urlsafe(18)
            session["anonymous_editor_id"] = anonymous_id
            session.permanent = True
        return EditorIdentity(id=f"local:{anonymous_id}", display_name="Técnico local")

    def editing_error_response(error: Exception):
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
            raise EditingStateError(
                "Metadados de edição em falta: " + ", ".join(sorted(set(missing)))
            )
        try:
            metadata["base_revision"] = int(metadata["base_revision"])
        except (TypeError, ValueError) as exc:
            raise EditingStateError("Revisão base inválida.") from exc
        return metadata

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
            return redirect(url_for("login", error=str(exc)))

        return redirect(authorization_url)

    @app.route("/auth/microsoft/callback")
    def microsoft_auth_callback():
        if ACTIVE_AUTH_PROVIDER != "microsoft" or microsoft_auth_service is None:
            return redirect(url_for("login"))

        error = request.args.get("error_description") or request.args.get("error")
        if error:
            return redirect(url_for("login", error=error))

        state = request.args.get("state")
        code = request.args.get("code")
        expected_state = session.pop("microsoft_auth_state", None)
        nonce = session.pop("microsoft_auth_nonce", None)
        next_url = safe_next_url(session.pop("microsoft_auth_next", None))

        if not state or state != expected_state or not code or not nonce:
            return redirect(url_for("login", error="Sessão Microsoft inválida. Tente novamente."))

        try:
            user = microsoft_auth_service.authenticate_code(
                code=code,
                redirect_uri=microsoft_redirect_uri(),
                nonce=nonce,
            )
        except MicrosoftAuthError as exc:
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

    def load_editor_state(path):
        excel_form_data = serialize_payload(ExcelService(path).read_link_as_form_data())
        document_data = serialize_payload(
            document_from_excel_and_extra(
                excel_form_data,
                DocumentDataService(path).read(),
            )
        )
        if not document_data.get("work_number"):
            app.logger.warning(
                "Número de obra ausente ou inválido em %s; ligação SharePoint desativada.",
                Path(path).name,
            )

        signatures = SignatureService(path).read_as_data_urls()
        return excel_form_data, document_data, signatures

    def schedule_graph_refresh(*, force: bool = False) -> dict[str, object] | None:
        if graph_refresh_coordinator is None:
            return None
        return graph_refresh_coordinator.request_refresh(force=force)

    def load_document_assets() -> tuple[str, str]:
        static_root = Path(app.static_folder)
        css_path = static_root / "css" / "service-document.css"
        logo_path = static_root / "img" / "sensorpoint-logo.png"

        embedded_styles = css_path.read_text(encoding="utf-8")
        logo_src = f"data:image/png;base64,{base64.b64encode(logo_path.read_bytes()).decode('ascii')}"
        return embedded_styles, logo_src

    def build_document_html(
        *,
        file_name: str,
        path,
        payload: dict[str, object],
        auto_print: bool = False,
    ) -> str:
        document_data = normalize_document_payload(payload)
        responsible_technician = get_primary_technician_name(document_data)
        embedded_styles, logo_src = load_document_assets()
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
            document_required_fields=[
                *DOCUMENT_REQUIRED_FIELDS,
                *(
                    [{"key": "customer_email", "label": "E-mail do cliente"}]
                    if mail_service is not None else []
                ),
            ],
            mail_enabled=mail_service is not None,
            mail_test_recipient=(
                mail_service.config.test_recipient if mail_service is not None else ""
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
        except WorkFolderNotFoundError as exc:
            app.logger.warning("Pasta da obra %s não encontrada: %s", work_number, exc)
            return render_work_folder_error(
                404,
                "Pasta de obra não encontrada",
                (
                    f"Não foi encontrada uma pasta com o número {work_number} em "
                    f"{GRAPH_WORKS_PATH.replace('/', ' / ')}."
                ),
            )
        except WorkFolderAmbiguousError as exc:
            app.logger.error("Número de obra duplicado no SharePoint: %s", exc)
            return render_work_folder_error(
                409,
                "Número de obra duplicado",
                (
                    f"Existe mais de uma pasta para a obra {work_number}. "
                    "A ligação foi bloqueada para evitar abrir a pasta errada."
                ),
            )
        except WorkFolderUnsafeUrlError as exc:
            app.logger.error("URL SharePoint rejeitado para a obra %s: %s", work_number, exc)
            return render_work_folder_error(
                502,
                "Destino SharePoint inválido",
                "O SharePoint devolveu um destino que a aplicação não pode abrir em segurança.",
            )
        except GraphStorageError as exc:
            app.logger.error("Falha Graph ao resolver a obra %s: %s", work_number, exc)
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
        if request.args.get("refresh") in {"1", "true", "yes"}:
            schedule_graph_refresh(force=True)
        files_data = [serialize_file_entry(entry) for entry in file_service.list_valid_files()]
        return jsonify({
            "success": True,
            "files": files_data,
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

    @app.route("/api/file/<name>/bootstrap")
    def bootstrap_file_editor(name: str):
        path = file_service.get_file_by_name(name)
        if not path:
            return json_error("Ficheiro não encontrado", 404)
        client_id = str(request.args.get("client_id") or "").strip()
        if not client_id:
            return json_error("Identificador da sessão de edição em falta.", 400)
        try:
            form_data, source_document, signatures = load_editor_state(path)
            identity = current_editor_identity()
            if file_service.is_draft_file(path):
                editing = editing_state_service.acquire_lease(path, identity, client_id)
            else:
                editing = editing_state_service.acquire_private_workspace(
                    path, identity, client_id
                )
            effective_document = editing.get("server_document") or source_document
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
            traceback.print_exc()
            return json_error(str(exc), 500)

    @app.route("/api/file/<name>")
    def get_file_data(name: str):
        path = file_service.get_file_by_name(name)
        if not path:
            return json_error("Ficheiro não encontrado", 404)

        try:
            form_data, document_data, signatures = load_editor_state(path)
            editing = editing_state_service.snapshot(path)
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
            traceback.print_exc()
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
                editing = editing_state_service.acquire_lease(path, identity, client_id)
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
                strip_signature_payload(normalize_document_payload(document_payload))
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
            document = strip_signature_payload(normalize_document_payload(document_payload))
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
            html = build_document_html(
                file_name=name,
                path=path,
                payload=payload,
                auto_print=bool(payload.get("_auto_print")),
            )
            return jsonify({"success": True, "html": html})
        except Exception as exc:
            traceback.print_exc()
            return json_error(str(exc), 500)

    @app.route("/api/file/<name>/draft", methods=["POST"])
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

            document_data = normalize_document_payload(data)
            if document_data.get("client_not_present"):
                data[CLIENT_SIGNATURE_LABEL] = ""
            excel_form_data = document_to_excel_form(document_data)
            signature_service = SignatureService(draft_path)
            resolved_signatures = signature_service.resolve_from_form_data(data)
            ExcelService(draft_path).write_link_from_form(
                excel_form_data,
                signatures=resolved_signatures,
            )
            DocumentDataService(draft_path).write(strip_signature_payload(document_data))
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
            file_service.invalidate_cache()
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
            traceback.print_exc()
            return json_error(str(exc), 500)

    @app.route("/api/file/<name>/send", methods=["POST"])
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

            internal_observations = pop_internal_observations(document_payload)
            document_data = normalize_document_payload(document_payload)
            missing_fields = document_missing_required_fields(document_data)
            invalid_fields = document_invalid_fields(document_data)
            mail_payload = None
            teams_payload = None
            if mail_service is not None:
                customer_email = str(document_data.get("customer_email") or "").strip()
                if not customer_email:
                    missing_fields.append("E-mail do cliente")
                else:
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

            if mail_service is not None:
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
                        customer_email=str(document_data["customer_email"]),
                        technician_email=technician_email,
                        customer_name=str(document_data.get("customer_name") or ""),
                        service_number=str(document_data.get("service_number") or name),
                        document_language=str(document_data.get("document_language") or "pt"),
                    )
                except GraphMailConfigurationError as exc:
                    return json_error(str(exc), 503)
                except GraphMailError as exc:
                    return json_error(str(exc), 400)

                if teams_notification_service is not None:
                    try:
                        teams_payload = (
                            teams_notification_service.prepare_service_sent_notification(
                                service_number=str(
                                    document_data.get("service_number") or name
                                ),
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

            source_path = Path(str(operation_context.get("source_path") or path))
            photo_target_path = archived_path or source_path
            PhotoAttachmentService(photo_target_path).apply(
                prepared_photos,
                removed_photo_ids,
            )
            graph_active_etag = str(operation_context.get("graph_active_etag") or "") or None
            graph_active_source_name = str(operation_context.get("graph_active_source_name") or "")
            if archived_path is None:
                if graph_service is not None:
                    graph_active_source_name = graph_service.active_source_name(source_path)
                    graph_active_etag = graph_service.active_source_etag(source_path)
                archived_path = archive_service.archive(source_path)
                editing_state_service.update_operation_context(
                    document_id,
                    idempotency_key,
                    source_path=str(source_path),
                    archived_path=str(archived_path),
                    graph_active_etag=graph_active_etag,
                    graph_active_source_name=graph_active_source_name,
                )

            excel_form_data = document_to_excel_form(document_data)
            ExcelService(archived_path).write_link_from_form(
                excel_form_data,
                signatures=resolved_signatures,
                final=True,
            )
            DocumentDataService(archived_path).write(strip_signature_payload(document_data))
            SignatureService(archived_path).save_from_form_data(document_payload)
            internal_observations_path = write_internal_observations(
                archived_path,
                internal_observations,
            )
            document_html = build_document_html(
                file_name=archived_path.stem,
                path=archived_path,
                payload=document_payload,
                auto_print=False,
            )
            DocumentArtifactService(archived_path).write_html(document_html)
            graph_uploaded_files = []
            graph_removed_active = False
            graph_sync_job = None
            if graph_service is not None and graph_sync_queue is not None:
                graph_job_payload = {
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
                        "archived_path": str(archived_path),
                        "mail": mail_payload,
                        "teams": teams_payload,
                    },
                    job_id=f"send:{document_id}:{idempotency_key}",
                )

            file_service.invalidate_cache()
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
            traceback.print_exc()
            return json_error(str(exc), 500)

    @app.route("/api/file/<name>/cancel", methods=["POST"])
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
            traceback.print_exc()
            return json_error(str(exc), 500)

    return app


app = create_app()
