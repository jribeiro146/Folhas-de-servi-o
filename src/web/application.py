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
    get_primary_technician_name,
    get_technician_initials,
    document_missing_required_fields,
    document_to_excel_form,
    normalize_document_payload,
    strip_signature_payload,
)
from src.field_map import FIELD_MAP, FIELDS_BY_GROUP
from src.services.archive_service import ArchiveService
from src.services.document_artifact_service import DocumentArtifactService
from src.services.document_data_service import DocumentDataService
from src.services.excel_service import ExcelService, ExcelValidationError
from src.services.file_service import FileService
from src.services.graph_storage_service import GraphStorageError, GraphStorageService
from src.services.microsoft_auth_service import MicrosoftAuthError, MicrosoftAuthService
from src.services.signature_service import SignatureService


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

    @app.after_request
    def apply_cache_headers(response):
        if request.path in {
            "/",
            "/login",
            "/service-worker.js",
            "/static/js/field-app.js",
            "/static/js/document-editor.js",
            "/static/css/auth.css",
            "/static/css/field-app.css",
            "/static/css/document-editor.css",
            "/static/css/service-document.css",
        }:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    def json_error(message: str, status: int = 400):
        return jsonify({"success": False, "error": message}), status

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
        notes_path.write_text(f"{observations}\n", encoding="utf-8")
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

        if selected_file_name:
            path = file_service.get_file_by_name(selected_file_name)
            if not path:
                selected_file_error = "Ficheiro não encontrado."
            else:
                try:
                    selected_file_data, selected_document_data, selected_signatures = load_editor_state(path)
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
            service_type_options=SERVICE_TYPE_OPTIONS,
            equipment_options=EQUIPMENT_OPTIONS,
            technician_options=TECHNICIAN_OPTIONS,
            document_required_fields=DOCUMENT_REQUIRED_FIELDS,
            current_user=g.current_user,
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
            return jsonify({
                "success": True,
                "file": name,
                "data": form_data,
                "document": document_data,
                "signatures": signatures,
            })
        except ExcelValidationError as exc:
            return json_error(str(exc), 400)
        except Exception as exc:
            traceback.print_exc()
            return json_error(str(exc), 500)

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

        data = request.get_json(silent=True)
        if not data:
            return json_error("Sem dados", 400)

        try:
            created_copy = False
            draft_path = path

            if not file_service.is_draft_file(path):
                draft_path = file_service.create_draft_copy(path, get_primary_technician_name(data))
                created_copy = draft_path != path

            document_data = normalize_document_payload(data)
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
                    fail_if_exists=created_copy,
                )
            file_service.invalidate_cache()
            return jsonify({
                "success": True,
                "message": "Rascunho guardado.",
                "file": draft_path.stem,
                "status": "in_progress",
                "created_copy": created_copy,
                "graph_uploaded_files": graph_uploaded_files,
            })
        except ExcelValidationError as exc:
            return json_error(str(exc), 400)
        except Exception as exc:
            traceback.print_exc()
            return json_error(str(exc), 500)

    @app.route("/api/file/<name>/send", methods=["POST"])
    def save_and_send(name: str):
        path = file_service.get_file_by_name(name)
        if not path:
            return json_error("Ficheiro não encontrado", 404)

        data = request.get_json(silent=True)
        if not data:
            return json_error("Sem dados", 400)

        try:
            document_payload = dict(data)
            internal_observations = pop_internal_observations(document_payload)
            document_data = normalize_document_payload(document_payload)
            missing_fields = document_missing_required_fields(document_data)
            excel_service = ExcelService(path)
            signature_service = SignatureService(path)
            resolved_signatures = signature_service.resolve_from_form_data(document_payload)
            missing_signature_fields = [
                label for label in SignatureService.required_labels()
                if not resolved_signatures.get(label)
            ]
            missing_fields.extend(missing_signature_fields)
            if missing_fields:
                return jsonify({
                    "success": False,
                    "error": "Existem campos obrigatórios por preencher.",
                    "missing_fields": missing_fields,
                }), 400

            source_path = path
            archived_path = archive_service.archive(path)
            excel_form_data = document_to_excel_form(document_data)
            excel_service = ExcelService(archived_path)
            signature_service = SignatureService(archived_path)
            excel_service.write_link_from_form(
                excel_form_data,
                signatures=resolved_signatures,
            )
            DocumentDataService(archived_path).write(strip_signature_payload(document_data))
            signature_service.save_from_form_data(document_payload)
            internal_observations_path = write_internal_observations(archived_path, internal_observations)
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
                graph_removed_active = graph_service.remove_active_entry(source_path)
            file_service.invalidate_cache()
            return jsonify({
                "success": True,
                "message": "Folha finalizada com sucesso.",
                "archived_excel": archived_path.name,
                "internal_observations": internal_observations_path.name if internal_observations_path else None,
                "graph_uploaded_files": graph_uploaded_files,
                "graph_removed_active": graph_removed_active,
            })
        except ExcelValidationError as exc:
            return json_error(str(exc), 400)
        except Exception as exc:
            traceback.print_exc()
            return json_error(str(exc), 500)

    @app.route("/api/file/<name>/cancel", methods=["POST"])
    def cancel_file(name: str):
        path = file_service.get_file_by_name(name)
        if not path:
            return json_error("Ficheiro não encontrado", 404)

        try:
            canceled_path = archive_service.cancel(path)
            file_service.invalidate_cache()
            return jsonify({
                "success": True,
                "message": "Folha cancelada com sucesso.",
                "canceled_excel": canceled_path.name,
            })
        except Exception as exc:
            traceback.print_exc()
            return json_error(str(exc), 500)

    return app


app = create_app()
