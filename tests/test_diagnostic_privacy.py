"""Diagnostic authorization and privacy with synthetic provider markers only."""
import io
import json
import logging
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, parse_qs, urlsplit

import pytest

from src.logging_config import JsonLogFormatter
from src.services import admin_access, microsoft_auth_service as auth_module
from src.services.graph_mail_service import GraphMailError, GraphMailConfigurationError
from src.services.graph_storage_service import GraphStorageError
from src.services.graph_sync_queue import GraphSyncQueue
from src.services.microsoft_auth_service import MicrosoftAuthService, MicrosoftAuthError
from src.services.file_service import FileService
from src.services.editing_state_service import EditingStateService
from tests.test_graph_job_visibility import TENANT, ADMIN_ID, OWNER, login, make_job
import src.web.application as web

MARKERS = ["private-person@example.invalid", "synthetic-bearer-private", "https://signed.example.invalid/private?sig=synthetic-sig",
           "C:/Private Synthetic/client.xlsx", "/private/synthetic/client.xlsx"]
DETAIL = f"{MARKERS[0]} Bearer {MARKERS[1]} {MARKERS[2]} '{MARKERS[3]}' '{MARKERS[4]}'"


@pytest.fixture
def diagnostic_app(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "ACTIVE_AUTH_PROVIDER", "microsoft")
    monkeypatch.setattr(web, "AUTH_ENABLED", True)
    monkeypatch.setattr(admin_access, "ADMIN_TENANT_ID", TENANT)
    monkeypatch.setattr(admin_access, "ADMIN_OBJECT_ID", ADMIN_ID)
    class Graph:
        calls = 0
        failure = None
        def status(self):
            return {"configured": True, "missing_fields": ["GRAPH_CLIENT_ID", DETAIL], "drive_id": True, "active_path": DETAIL}
        def test_connection(self):
            self.calls += 1
            if self.failure:
                raise self.failure
            return {"drive": DETAIL, "active_folder": DETAIL, "active_path": DETAIL}
    class Mail:
        calls = 0
        failure = None
        def status(self):
            return {"enabled": True, "configured": True, "sender": DETAIL, "test_recipient": DETAIL}
        def test_connection(self):
            self.calls += 1
            if self.failure:
                raise self.failure
            return {"authenticated": True, "mail_send_permission": True, "sender": DETAIL, "tenant_id": DETAIL}
    class Refresh:
        calls = 0
        def status(self):
            return {"refresh_id": "a" * 32, "generation_id": "b" * 32, "refresh_sequence": 3,
                "last_completed_sequence": 3, "in_progress": False, "outcome": "error", "last_completed_outcome": "error",
                "last_completed_refresh_id": "a" * 32, "last_completed_at": 1234, "last_error": DETAIL,
                "synced_files": [DETAIL], "inventory": {"available": True, "stale": True, "updated_at": 1234,
                "refresh_id": "a" * 32, "file_count": 2, "unavailable_count": 1, "unavailable_files": [DETAIL], "error": DETAIL}}
        def request_refresh(self, **_kwargs):
            self.calls += 1
            return self.status()
    graph, mail, refresh = Graph(), Mail(), Refresh()
    queue = GraphSyncQueue(None, tmp_path / "queue.sqlite3", auto_start=False)
    auth = MicrosoftAuthService(tenant_id=TENANT, client_id=ADMIN_ID, client_secret="synthetic")
    editing = EditingStateService(tmp_path / "editing")
    app = web.create_app(file_service=FileService(tmp_path / "active"), graph_service=graph,
        mail_service=mail, graph_refresh_coordinator=refresh, graph_sync_queue=queue,
        microsoft_auth_service=auth, work_folder_service=object(), editing_state_service=editing)
    app.extensions["synthetic_editing"] = editing
    return app, graph, mail, refresh, queue, auth


def assert_private(text):
    for marker in MARKERS:
        assert marker not in unquote(text)


@pytest.mark.parametrize("route", ["/api/graph/test", "/api/mail/test"])
@pytest.mark.parametrize("actor,expected", [("admin", 200), ("technician", 403), ("anonymous", 401)])
def test_provider_diagnostics_require_configured_admin_before_any_call(diagnostic_app, route, actor, expected):
    app, graph, mail, *_ = diagnostic_app
    client = (login(app, oid=ADMIN_ID, email=admin_access.ADMIN_EMAIL) if actor == "admin" else
              login(app) if actor == "technician" else app.test_client())
    response = client.get(route)
    assert response.status_code == expected
    assert graph.calls + mail.calls == (1 if actor == "admin" else 0)
    assert_private(response.get_data(as_text=True))


@pytest.mark.parametrize("admin", [False, True])
def test_status_projects_fields_and_keeps_refresh_contract(diagnostic_app, admin):
    app = diagnostic_app[0]
    client = login(app, oid=ADMIN_ID, email=admin_access.ADMIN_EMAIL) if admin else login(app)
    response = client.get("/api/graph/status")
    assert response.status_code == 200
    payload = response.get_json()
    assert ("mail" in payload) is admin
    assert payload["refresh"]["refresh_sequence"] == 3
    assert payload["refresh"]["generation_id"] == "b" * 32
    assert payload["refresh"]["inventory"]["unavailable_count"] == 1
    assert payload["refresh"]["last_error"] and payload["refresh"]["error_code"] == "graph_refresh_failed"
    assert_private(response.get_data(as_text=True))


@pytest.mark.parametrize("route,method", [("/api/files?refresh=1", "get"), ("/api/graph/sync", "post")])
def test_technician_can_refresh_list_without_exporting_provider_details(diagnostic_app, route, method):
    app, _, _, refresh, _, _ = diagnostic_app
    response = getattr(login(app), method)(route)
    assert response.status_code in (200, 202)
    assert refresh.calls == 1
    assert response.get_json()["refresh"]["refresh_id"] == "a" * 32
    assert_private(response.get_data(as_text=True))


@pytest.mark.parametrize("kind,status", [("graph", 502), ("mail", 502), ("mail_config", 503)])
def test_diagnostic_failure_has_safe_code_reference_and_logs(diagnostic_app, caplog, kind, status):
    app, graph, mail, *_ = diagnostic_app
    if kind == "graph":
        graph.failure = GraphStorageError(DETAIL)
    else:
        mail.failure = (GraphMailConfigurationError if kind == "mail_config" else GraphMailError)(DETAIL)
    caplog.set_level(logging.ERROR)
    client = login(app, oid=ADMIN_ID, email=admin_access.ADMIN_EMAIL)
    response = client.get("/api/graph/test" if kind == "graph" else "/api/mail/test")
    assert response.status_code == status
    assert response.get_json()["reference"] == response.headers["X-Request-ID"]
    assert response.get_json()["code"].endswith("failed")
    assert_private(response.get_data(as_text=True))
    assert_private("\n".join(JsonLogFormatter().format(r) for r in caplog.records))


@pytest.mark.parametrize("stage", ["provider", "preflight", "start", "callback"])
def test_login_redirect_never_contains_provider_description_or_exception(diagnostic_app, monkeypatch, caplog, stage):
    app, graph, _, _, _, auth = diagnostic_app
    client = app.test_client()
    if stage == "preflight":
        graph.failure = GraphStorageError(DETAIL)
    def reject(**_kwargs):
        raise MicrosoftAuthError(DETAIL)
    monkeypatch.setattr(auth, "build_authorization_url", reject)
    monkeypatch.setattr(auth, "authenticate_code", reject)
    caplog.set_level(logging.WARNING)
    if stage in ("provider", "callback"):
        with client.session_transaction() as session:
            session.update(microsoft_auth_state="synthetic-state", microsoft_auth_nonce="synthetic-nonce")
        query = {"error_description": DETAIL, "error": DETAIL} if stage == "provider" else {"state": "synthetic-state", "code": "synthetic-code"}
        response = client.get("/auth/microsoft/callback", query_string=query)
    else:
        response = client.get("/auth/microsoft")
    assert response.status_code == 302
    query = parse_qs(urlsplit(response.location).query)
    assert query["reference"] == [response.headers["X-Request-ID"]]
    assert query["code"][0].startswith("auth_")
    assert_private(response.location)
    assert_private("\n".join(JsonLogFormatter().format(r) for r in caplog.records))


def test_admin_job_diagnostic_never_exports_raw_error_or_phase(diagnostic_app):
    app, _, _, _, queue, _ = diagnostic_app
    identifier = make_job(queue, OWNER, result={"phase": DETAIL})
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET last_error=? WHERE id=?", (DETAIL, identifier))
    response = login(app, oid=ADMIN_ID, email=admin_access.ADMIN_EMAIL).get("/api/admin/overview")
    assert response.status_code == 200
    job = response.get_json()["jobs"][0]
    assert job["phase"] is None and job["error_code"] == "job_reconciliation_required"
    assert_private(response.get_data(as_text=True))


@pytest.mark.parametrize("error", [HTTPError("https://example.invalid", 400, DETAIL, None, io.BytesIO(DETAIL.encode())), URLError(DETAIL)])
def test_microsoft_transport_error_does_not_retain_raw_body_in_public_message(monkeypatch, error):
    def fail(*_args, **_kwargs):
        raise error
    monkeypatch.setattr(auth_module.request, "urlopen", fail)
    with pytest.raises(MicrosoftAuthError) as caught:
        MicrosoftAuthService._request_json("https://example.invalid", method="GET")
    assert_private(str(caught.value))


def test_exported_log_redacts_markers_in_message_fields_and_exception():
    try:
        raise RuntimeError(DETAIL)
    except RuntimeError:
        import sys
        record = logging.LogRecord("synthetic", logging.ERROR, __file__, 1, DETAIL, (), sys.exc_info())
    record.event = "synthetic_failure"
    record.event_fields = {"detail": DETAIL, "nested": {"message": DETAIL}, "error_code": "synthetic_failure"}
    output = JsonLogFormatter().format(record)
    assert_private(output)
    assert json.loads(output)["error_code"] == "synthetic_failure"
    assert "RuntimeError" in json.loads(output)["exception"]


def test_admin_refresh_rejection_does_not_export_provider_value_error(diagnostic_app, monkeypatch):
    app, _, _, refresh, *_ = diagnostic_app
    def reject(**_kwargs):
        raise ValueError(DETAIL)
    monkeypatch.setattr(refresh, "request_refresh", reject)
    client = login(app, oid=ADMIN_ID, email=admin_access.ADMIN_EMAIL)
    with client.session_transaction() as session:
        session["admin_csrf"] = "synthetic-csrf"
    response = client.post("/api/admin/refresh", json={"reason": "Atualização fictícia"},
                           headers={"X-Admin-CSRF": "synthetic-csrf"})
    assert response.status_code == 409
    assert response.get_json()["code"] == "admin_action_rejected"
    assert_private(response.get_data(as_text=True))


def test_admin_history_projection_preserves_private_original(diagnostic_app):
    app = diagnostic_app[0]
    repository = app.extensions["synthetic_editing"].repository
    repository.record_admin_action({"actor_email": MARKERS[0], "action": "refresh_requested",
        "target": DETAIL, "reason": DETAIL, "details": {"provider_error": DETAIL}})
    response = login(app, oid=ADMIN_ID, email=admin_access.ADMIN_EMAIL).get("/api/admin/overview")
    assert response.status_code == 200
    entry = response.get_json()["audit"][0]
    assert set(entry) == {"action", "created_at", "actor_label", "target", "reason"}
    assert entry["actor_label"] == "Administrador"
    assert_private(response.get_data(as_text=True))
    assert repository.admin_history()[0]["details"]["provider_error"] == DETAIL
