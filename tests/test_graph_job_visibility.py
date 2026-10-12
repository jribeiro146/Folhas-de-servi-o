"""Private queue polling through the real Flask route and synthetic SQLite."""
import json
from types import SimpleNamespace

import pytest

from src.services import admin_access
from src.services.graph_job_visibility import public_job_status
from src.services.graph_sync_queue import GraphSyncQueue
from src.services.microsoft_auth_service import MicrosoftAuthService
import src.web.application as web

TENANT = "11111111-1111-4111-8111-111111111111"
OWNER_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ADMIN_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
OTHER_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
OWNER = {"provider": "microsoft", "tenant_id": TENANT, "object_id": OWNER_ID}
MARKER = "private@example.invalid /private/synthetic-path Bearer synthetic-private-marker"


def login(app, *, oid=OWNER_ID, tenant=TENANT, email="owner@example.invalid"):
    client = app.test_client()
    with client.session_transaction() as session:
        session["microsoft_user"] = {"id": oid, "tenant_id": tenant, "email": email,
                                     "username": email, "display_name": "Técnico fictício"}
    return client


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "ACTIVE_AUTH_PROVIDER", "microsoft")
    monkeypatch.setattr(web, "AUTH_ENABLED", True)
    monkeypatch.setattr(admin_access, "ADMIN_TENANT_ID", TENANT)
    monkeypatch.setattr(admin_access, "ADMIN_OBJECT_ID", ADMIN_ID)
    queue = GraphSyncQueue(None, tmp_path / "queue.sqlite3", auto_start=False)
    app = web.create_app(graph_sync_queue=queue, microsoft_auth_service=MicrosoftAuthService())
    return app, queue


def make_job(queue, owner=OWNER, *, result=None, status="failed", retryable=0):
    payload = {"_owner": owner, "mail": {"to": MARKER}, "archived_path": MARKER}
    job = queue.enqueue("archive_and_mail_local", payload, job_id="send:synthetic-private")
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET status=?,retryable=?,result_json=?,last_error=?,attempt_token=?,"
                   "next_attempt_at=2000000000 WHERE id=?",
                   (status, retryable, json.dumps(result or {}), MARKER, MARKER, job["id"]))
    return job["id"]


@pytest.mark.parametrize("actor,expected", [("owner", 200), ("other", 403), ("other_tenant", 403),
                                           ("admin", 200), ("anonymous", 401)])
def test_only_owner_or_configured_admin_can_poll(service, actor, expected):
    app, queue = service
    job_id = make_job(queue, result={"phase": "mail_accepted", "mail": {"accepted": True, "to": MARKER},
                                     "local_pdf": MARKER, "previous_result_json": MARKER})
    if actor == "anonymous":
        client = app.test_client()
    elif actor == "admin":
        client = login(app, oid=ADMIN_ID, email=admin_access.ADMIN_EMAIL)
    else:
        client = login(app, oid=OTHER_ID if actor == "other" else OWNER_ID,
                       tenant=OTHER_ID if actor == "other_tenant" else TENANT)
    response = client.get(f"/api/graph/jobs/{job_id}")
    assert response.status_code == expected
    assert "no-store" in response.headers["Cache-Control"]
    assert MARKER not in response.get_data(as_text=True)
    if expected == 200:
        job = response.get_json()["job"]
        assert set(job) == {"id", "status", "will_retry", "reconciliation_required", "next_attempt_at", "result"}
        assert job["result"] == {"phase": "mail_accepted", "mail": {"accepted": True}}
        assert job["next_attempt_at"] == 2000000000
    # Internal consumers still have their full snapshot; redaction is only HTTP.
    assert queue.status(job_id)["payload"]["mail"]["to"] == MARKER


@pytest.mark.parametrize("owner", [None, {}, "invalid", {"provider": "microsoft", "object_id": OWNER_ID},
                                   {"provider": "local", "id": ""}])
def test_unproven_legacy_ownership_remains_admin_only(service, owner):
    app, queue = service
    identifier = make_job(queue, owner)
    assert login(app).get(f"/api/graph/jobs/{identifier}").status_code == 403
    assert login(app, oid=ADMIN_ID, email=admin_access.ADMIN_EMAIL).get(f"/api/graph/jobs/{identifier}").status_code == 200


@pytest.mark.parametrize("result,retryable,uncertain", [
    ({"phase": "mail_unknown"}, 1, True),
    ({"commit_guard_required": True}, 0, False),
    ({"invalid_state": True, "previous_result_json": MARKER}, 0, False),
    ({"migration_uncertain": True}, 0, True),
    ({"phase": "mail_rejected", "mail": {"accepted": False}}, 1, False),
])
def test_monitor_retains_acceptance_uncertainty_commit_and_retry_contract(service, result, retryable, uncertain):
    app, queue = service
    identifier = make_job(queue, result=result, retryable=retryable)
    response = login(app).get(f"/api/graph/jobs/{identifier}")
    assert response.status_code == 200
    public = response.get_json()["job"]
    assert public["reconciliation_required"] is uncertain
    assert public["will_retry"] is bool(retryable and not uncertain)
    for key in ("phase", "commit_guard_required", "invalid_state", "mail"):
        if key in result:
            assert public["result"][key] == result[key]
    assert MARKER not in response.get_data(as_text=True)


def test_unlisted_result_strings_and_fields_are_never_returned():
    job = {"id": "synthetic", "status": "failed", "last_error": MARKER, "payload": {"private": MARKER},
           "result": {"phase": MARKER, "accepted": MARKER, "commit_guard_required": MARKER,
                      "mail": {"accepted": True, "recipient": MARKER}, "retry_not_before": MARKER}}
    public = public_job_status(job)
    assert public["result"] == {"mail": {"accepted": True}}
    assert MARKER not in json.dumps(public)


def test_local_demo_jobs_are_bound_to_existing_signed_session(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "ACTIVE_AUTH_PROVIDER", "none")
    monkeypatch.setattr(web, "AUTH_ENABLED", False)
    queue = GraphSyncQueue(None, tmp_path / "queue.sqlite3", auto_start=False)
    app = web.create_app(graph_sync_queue=queue)
    identifier = make_job(queue, {"provider": "local", "id": "local:synthetic-owner"})
    client = app.test_client()
    with client.session_transaction() as session:
        session["anonymous_editor_id"] = "synthetic-owner"
    assert client.get(f"/api/graph/jobs/{identifier}").status_code == 200
    assert app.test_client().get(f"/api/graph/jobs/{identifier}").status_code == 403


def test_reenqueue_cannot_transfer_owner_and_teams_recovery_inherits_original_owner(service):
    app, queue = service
    notification = {"service_number": "SYNTHETIC", "payload": {"type": "message"}}
    parent = queue.enqueue("archive_and_mail_local", {"_owner": OWNER, "mail": {}, "teams": notification}, job_id="send:owner")
    queue.enqueue("archive_and_mail_local", {"_owner": {**OWNER, "object_id": OTHER_ID}}, job_id=parent["id"])
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET status='running',updated_at=0,result_json=? WHERE id=?",
                   (json.dumps({"mail": {"accepted": True}, "phase": "mail_accepted"}), parent["id"]))
    assert queue.status(parent["id"])["payload"]["_owner"] == OWNER
    child_id = "teams:" + parent["id"]
    assert queue.status(child_id)["payload"]["_owner"] == OWNER
    assert login(app).get(f"/api/graph/jobs/{child_id}").status_code == 200
    assert login(app, oid=OTHER_ID).get(f"/api/graph/jobs/{child_id}").status_code == 403


@pytest.mark.parametrize("action", ["draft", "send_graph", "send_local", "cancel"])
def test_web_producers_assign_authenticated_owner_instead_of_document_owner(service, tmp_path, monkeypatch, action):
    from src.services.file_service import FileService
    from src.services.editing_state_service import EditingStateService
    from tests.test_maintenance import synthetic_workbook
    import src.services.archive_service as archive_module
    import src.services.graph_cancellation as cancellation
    _, queue = service
    monkeypatch.setattr(archive_module, "EXCEL_ARQUIVADAS_DIR", tmp_path / "archive")
    monkeypatch.setattr(web, "EXCEL_CANCELADAS_DIR", tmp_path / "cancelled")
    monkeypatch.setattr(cancellation, "prepare_cancellation", lambda *_args: {"synthetic": True})
    source = synthetic_workbook(tmp_path / "active" / "2026_9900_2026-10-12_TF" / "2026_9900_2026-10-12_TF.xlsx")
    class Graph:
        def active_source_name(self, path):
            return path.parent.name
        def active_source_etag(self, _path):
            return "synthetic-etag"
    class Mail:
        def validate_address(self, *_args, **_kwargs):
            pass
        def prepare_service_email(self, **kwargs):
            return {"to": kwargs["customer_email"], "cc": [kwargs["technician_email"]]}
        def test_connection(self):
            return {"authenticated": True}
    app = web.create_app(file_service=FileService(source.parent.parent), graph_sync_queue=queue,
        graph_service=Graph() if action != "send_local" else None,
        graph_refresh_coordinator=SimpleNamespace(status=lambda: {}),
        mail_service=Mail() if action == "send_local" else None,
        microsoft_auth_service=MicrosoftAuthService(), editing_state_service=EditingStateService(tmp_path / "editing"))
    client = login(app)
    lease = client.post(f"/api/file/{source.stem}/lease", json={"client_id": "synthetic-tab"})
    assert lease.status_code == 200, lease.get_json()
    state = lease.get_json()["editing"]
    metadata = {"document_id": state["document_id"], "client_id": "synthetic-tab",
                "lease_token": state["lease"]["token"], "base_revision": state["revision"], "idempotency_key": "synthetic-operation"}
    payload = {"_edit": metadata, "_owner": {**OWNER, "object_id": OTHER_ID},
               "service_number": "2026_9900", "customer_name": "Cliente fictício", "client_not_present": True,
               "intervention_report": "Serviço fictício concluído", "technician_records": [{"technician": "João Freire",
               "start_time": "09:00", "end_time": "10:00", "date": "2026-10-12"}]}
    if action == "send_local":
        payload["customer_email"] = "customer@example.invalid"
    endpoint = "send" if action.startswith("send") else action
    response = client.post(f"/api/file/{source.stem}/{endpoint}", json=payload)
    assert response.status_code == 200, response.get_json()
    identifier = response.get_json()["graph_job_id"]
    assert queue.status(identifier)["payload"]["_owner"] == OWNER
    assert client.get(f"/api/graph/jobs/{identifier}").status_code == 200
    assert login(app, oid=OTHER_ID).get(f"/api/graph/jobs/{identifier}").status_code == 403
