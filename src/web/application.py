"""Aplicação Flask e API da webapp de Folhas de Serviço."""

from __future__ import annotations

import base64
import datetime as dt
import os
import secrets
import traceback
from pathlib import Path

from flask import Flask, g, jsonify, redirect, render_template, request, send_from_directory, session, url_for

from src.config import (
    AUTH_PROVIDER,
    MICROSOFT_AUTH_REDIRECT_URI,
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
from src.services.graph_storage_service import GraphConflictError, GraphStorageError, GraphStorageService
from src.services.microsoft_auth_service import MicrosoftAuthError, MicrosoftAuthService
from src.services.signature_service import CLIENT_SIGNATURE_LABEL, SignatureService


ACTIVE_AUTH_PROVIDER = "microsoft" if AUTH_PROVIDER == "microsoft" else "none"
AUTH_ENABLED = ACTIVE_AUTH_PROVIDER == "microsoft"
INTERNAL_OBSERVATIONS_KEY = "_internal_observations"


def _load_secret_key() -> str:
    return os.environ.get("FS_SECRET_KEY", "dev-local-secret")


def create_app(
    file_service: FileService | None = None,
    archive_service: ArchiveService | None = None,
    graph_service: GraphStorageService | None = None,
    microsoft_auth_service: MicrosoftAuthService | None = None,
    editing_state_service: EditingStateService | None = None,
) -> Flask:
    """Cria a aplicação Flask com dependências injetáveis para testes."""
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["ASSET_VERSION"] = dt.datetime.now().strftime("%Y%m%d%H%M%S")
    app.secret_key = _load_secret_key()
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=dt.timedelta(hours=14),
    )

    file_service = file_service or FileService()
    archive_service = archive_service or ArchiveService()
    graph_service = graph_service or (GraphStorageService() if STORAGE_BACKEND == "graph" else None)
    microsoft_auth_service = microsoft_auth_service or (
        MicrosoftAuthService() if ACTIVE_AUTH_PROVIDER == "microsoft" else None
    )

    editing_state_service = editing_state_service or EditingStateService()
    @app.after_request
    def apply_cache_headers(response):
        if request.path.startswith("/api/") or request.path in {
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

    def validate_document_identity(path: Path, metadata: dict[str, object]) -> str:
        document_id = str(metadata["document_id"])
        expected_id = editing_state_service.resolve_document_id(path)
        if document_id != expected_id:
            raise RevisionConflictError(editing_state_service.snapshot(path, expected_id))
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

    def load_editor_state(path):
        excel_form_data = serialize_payload(ExcelService(path).read_link_as_form_data())
        document_data = serialize_payload(
            document_from_excel_and_extra(
                excel_form_data,
                DocumentDataService(path).read(),
            )
        )
        signatures = SignatureService(path).read_as_data_urls()
        return excel_form_data, document_data, signatures

    def sync_graph_active_files() -> str | None:
        if graph_service is None:
            return None

        try:
            graph_service.sync_active_files(file_service.directory)
            file_service.invalidate_cache()
            return None
        except GraphStorageError as exc:
            traceback.print_exc()
            return str(exc)

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
        storage_error = sync_graph_active_files()
        selected_file_name = request.args.get("file", "").strip() or None
        selected_file_data = None
        selected_document_data = None
        selected_signatures = {}
        selected_file_error = storage_error
        selected_editing_state = None
        editor_identity = current_editor_identity()

        if selected_file_name:
            path = file_service.get_file_by_name(selected_file_name)
            if not path:
                selected_file_error = "Ficheiro não encontrado."
            else:
                try:
                    selected_file_data, selected_document_data, selected_signatures = load_editor_state(path)
                    selected_editing_state = editing_state_service.snapshot(path)
                except ExcelValidationError as exc:
                    selected_file_error = str(exc)
                except Exception as exc:
                    traceback.print_exc()
                    selected_file_error = str(exc)

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
            service_type_options=SERVICE_TYPE_OPTIONS,
            equipment_options=EQUIPMENT_OPTIONS,
            technician_options=TECHNICIAN_OPTIONS,
            document_required_fields=DOCUMENT_REQUIRED_FIELDS,
            current_user=g.current_user,
            editor_user={
                "id": editor_identity.id,
                "display_name": editor_identity.display_name,
            },
            asset_version=app.config["ASSET_VERSION"],
        )

    @app.route("/manifest.webmanifest")
    def manifest():
        return send_from_directory(app.static_folder, "manifest.webmanifest")

    @app.route("/service-worker.js")
    def service_worker():
        return send_from_directory(app.static_folder, "service-worker.js")

    @app.route("/api/files")
    def get_files():
        storage_error = sync_graph_active_files()
        if storage_error:
            return json_error(storage_error, 502)
        files_data = [serialize_file_entry(entry) for entry in file_service.list_valid_files()]
        return jsonify({"success": True, "files": files_data})

    @app.route("/api/graph/status")
    def graph_status():
        if graph_service is None:
            return jsonify({
                "success": True,
                "backend": STORAGE_BACKEND,
                "graph_enabled": False,
            })

        status = graph_service.status()
        return jsonify({
            "success": True,
            "backend": STORAGE_BACKEND,
            "graph_enabled": True,
            "graph": status,
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
        if graph_service is None:
            return json_error("Backend Graph não está ativo.", 400)

        try:
            files = graph_service.sync_active_files(file_service.directory)
            file_service.invalidate_cache()
            return jsonify({
                "success": True,
                "synced_files": [path.name for path in files],
            })
        except GraphStorageError as exc:
            return json_error(str(exc), 502)

    @app.route("/api/file/<name>")
    def get_file_data(name: str):
        path = file_service.get_file_by_name(name)
        if not path:
            return json_error("Ficheiro não encontrado", 404)

        try:
            form_data, document_data, signatures = load_editor_state(path)
            editing = editing_state_service.snapshot(path)
            return jsonify({
                "success": True,
                "file": name,
                "data": form_data,
                "document": document_data,
                "signatures": signatures,
                "editing": editing,
            })
        except ExcelValidationError as exc:
            return json_error(str(exc), 400)
        except Exception as exc:
            traceback.print_exc()
            return json_error(str(exc), 500)

    @app.route("/api/file/<name>/lease", methods=["POST"])
    def acquire_file_lease(name: str):
        path = file_service.get_file_by_name(name)
        if not path:
            return json_error("Ficheiro não encontrado", 404)
        data = request.get_json(silent=True) or {}
        client_id = str(data.get("client_id") or "").strip()
        try:
            editing = editing_state_service.acquire_lease(
                path,
                current_editor_identity(),
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
            if document_id != editing_state_service.resolve_document_id(path):
                raise RevisionConflictError(editing_state_service.snapshot(path))
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

        received = request.get_json(silent=True)
        if not received:
            return json_error("Sem dados", 400)

        document_id = ""
        idempotency_key = ""
        try:
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
                if graph_service is not None:
                    graph_service.assert_active_entry_current(path)
                draft_path = file_service.create_draft_copy(
                    path,
                    get_primary_technician_name(data),
                )
                created_copy = draft_path != path
                created_this_attempt = created_copy
                editing_state_service.associate_path(
                    draft_path,
                    document_id,
                    original_name=path.stem,
                )
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
            graph_uploaded_files = []
            if graph_service is not None:
                graph_uploaded_files = graph_service.upload_active_bundle(
                    draft_path,
                    fail_if_exists=created_this_attempt,
                )
            file_service.invalidate_cache()
            result = editing_state_service.commit_operation(
                document_id=document_id,
                idempotency_key=idempotency_key,
                path=draft_path,
                response={
                    "success": True,
                    "message": "Rascunho guardado.",
                    "file": draft_path.stem,
                    "status": "in_progress",
                    "created_copy": created_copy,
                    "graph_uploaded_files": graph_uploaded_files,
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
        received = request.get_json(silent=True)
        if not received:
            return json_error("Sem dados", 400)

        document_id = ""
        idempotency_key = ""
        try:
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
            if path is not None:
                validate_document_identity(path, metadata)

            internal_observations = pop_internal_observations(document_payload)
            document_data = normalize_document_payload(document_payload)
            missing_fields = document_missing_required_fields(document_data)
            invalid_fields = document_invalid_fields(document_data)
            if document_data.get("client_not_present"):
                document_payload[CLIENT_SIGNATURE_LABEL] = ""
            validation_path = archived_path or path
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
            graph_active_etag = str(operation_context.get("graph_active_etag") or "") or None
            if archived_path is None:
                if graph_service is not None:
                    active_item = graph_service.assert_active_entry_current(source_path)
                    graph_active_etag = str(active_item.get("eTag") or "") or None
                archived_path = archive_service.archive(source_path)
                editing_state_service.update_operation_context(
                    document_id,
                    idempotency_key,
                    source_path=str(source_path),
                    archived_path=str(archived_path),
                    graph_active_etag=graph_active_etag,
                )

            excel_form_data = document_to_excel_form(document_data)
            ExcelService(archived_path).write_link_from_form(
                excel_form_data,
                signatures=resolved_signatures,
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
            if graph_service is not None:
                graph_uploaded_files = graph_service.upload_archive_bundle(archived_path)
                graph_removed_active = graph_service.remove_active_entry(
                    source_path,
                    expected_etag=graph_active_etag,
                )
            file_service.invalidate_cache()
            result = editing_state_service.commit_operation(
                document_id=document_id,
                idempotency_key=idempotency_key,
                path=archived_path,
                release_lease=True,
                response={
                    "success": True,
                    "message": "Folha finalizada com sucesso.",
                    "archived_excel": archived_path.name,
                    "internal_observations": (
                        internal_observations_path.name if internal_observations_path else None
                    ),
                    "graph_uploaded_files": graph_uploaded_files,
                    "graph_removed_active": graph_removed_active,
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
            if canceled_path is None:
                if graph_service is not None:
                    active_item = graph_service.assert_active_entry_current(source_path)
                    graph_active_etag = str(active_item.get("eTag") or "") or None
                canceled_path = archive_service.cancel(source_path)
                editing_state_service.update_operation_context(
                    document_id,
                    idempotency_key,
                    source_path=str(source_path),
                    canceled_path=str(canceled_path),
                    graph_active_etag=graph_active_etag,
                )
            graph_removed_active = False
            if graph_service is not None:
                graph_removed_active = graph_service.remove_active_entry(
                    source_path,
                    expected_etag=graph_active_etag,
                )
            file_service.invalidate_cache()
            result = editing_state_service.commit_operation(
                document_id=document_id,
                idempotency_key=idempotency_key,
                path=canceled_path,
                release_lease=True,
                response={
                    "success": True,
                    "message": "Folha cancelada com sucesso.",
                    "canceled_excel": canceled_path.name,
                    "graph_removed_active": graph_removed_active,
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
