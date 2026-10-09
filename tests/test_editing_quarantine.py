"""Recovery boundaries: corrupt stored evidence must survive normal app use."""
import json
import sqlite3
import shutil
from pathlib import Path

import pytest

from src.services.editing_state_service import EditingStateService, EditingStateQuarantinedError


def replace_stored_state(service, document_id, raw):
    # Fault injection at the persisted-data boundary; no operational database.
    with sqlite3.connect(service.repository.database_path) as connection:
        connection.execute("UPDATE editing_states SET state_json=? WHERE document_id=?", (raw, document_id))


def stored_evidence(service, document_id):
    with sqlite3.connect(service.repository.database_path) as connection:
        return connection.execute("SELECT state_json,updated_at FROM editing_states WHERE document_id=?",
                                  (document_id,)).fetchone()


def test_logout_cleanup_preserves_truncated_state_exactly(tmp_path):
    service = EditingStateService(tmp_path / "editing")
    service.snapshot_document("broken-document")
    replace_stored_state(service, "broken-document", '{"version":2,"autosave":')
    before = stored_evidence(service, "broken-document")

    assert service.release_user_leases("unrelated-user") == 0

    assert stored_evidence(service, "broken-document") == before


@pytest.fixture
def quarantine_app(tmp_path, monkeypatch):
    import src.web.application as web
    from src.services.file_service import FileService
    from src.services.editing_state_service import EditorIdentity
    monkeypatch.setattr(web, "ACTIVE_AUTH_PROVIDER", "none")
    monkeypatch.setattr(web, "AUTH_ENABLED", False)
    monkeypatch.setattr(web, "STORAGE_BACKEND", "local")
    active = tmp_path / "Excel" / "Activas"
    active.mkdir(parents=True)
    original = active / "synthetic.xlsx"
    shutil.copy2(Path(__file__).parent / "fixtures/test_sample.xlsx", original)
    files = FileService(active)
    draft = files.create_draft_copy(original, "Synthetic")
    service = EditingStateService(tmp_path / "editing")
    identity = EditorIdentity("synthetic-user", "Synthetic")
    state = service.acquire_lease(draft, identity, "tab-1")
    original_state = stored_evidence(service, state["document_id"])[0]
    replace_stored_state(service, state["document_id"], '{"version":')
    app = web.create_app(file_service=files, editing_state_service=service, test_editor_identity=identity)
    app.config["SYNTHETIC_INITIAL_STATE"] = original_state
    return app, service, state["document_id"], draft


@pytest.mark.parametrize("endpoint", ["/", "/api/files"])
def test_corrupt_document_does_not_break_file_listing(quarantine_app, endpoint):
    app, service, document_id, _ = quarantine_app
    before = stored_evidence(service, document_id)
    response = app.test_client().get(endpoint)
    assert response.status_code == 200
    assert stored_evidence(service, document_id) == before


def test_bootstrap_explains_quarantine_without_exposing_state(quarantine_app):
    app, service, document_id, path = quarantine_app
    before = stored_evidence(service, document_id)
    response = app.test_client().get(f"/api/file/{path.stem}/bootstrap?client_id=tab-1")
    assert response.status_code == 423
    assert response.json["code"] == "editing_state_quarantined"
    assert response.json["success"] is False
    assert "server_document" not in response.json
    assert stored_evidence(service, document_id) == before


def test_file_data_explains_quarantine_without_internal_error(quarantine_app):
    app, service, document_id, path = quarantine_app
    before = stored_evidence(service, document_id)
    response = app.test_client().get(f"/api/file/{path.stem}")
    assert response.status_code == 423
    assert response.json["code"] == "editing_state_quarantined"
    assert stored_evidence(service, document_id) == before


@pytest.mark.parametrize("route", ["autosave", "editing/close"])
@pytest.mark.parametrize("field", ["client_id", "idempotency_key"])
def test_blank_editing_metadata_is_a_request_error_not_quarantine(quarantine_app, route, field):
    app, service, document_id, path = quarantine_app
    replace_stored_state(service, document_id, app.config["SYNTHETIC_INITIAL_STATE"])
    state = json.loads(app.config["SYNTHETIC_INITIAL_STATE"])
    token = next(iter(state["sessions"]))
    metadata = {"document_id": document_id, "client_id": "tab-1", "lease_token": token,
                "base_revision": state["revision"], "idempotency_key": "synthetic-save", field: " "}
    before = stored_evidence(service, document_id)
    response = app.test_client().post(f"/api/file/{path.stem}/{route}", json={
        "_edit": metadata, "document": {"intervention_report": "Synthetic"}})
    assert response.status_code == 409
    assert response.json["code"] == "editing_session_required"
    assert stored_evidence(service, document_id) == before
    assert all(not record.get("quarantined") for record in service.admin_documents())


def test_rejected_new_write_is_not_reported_as_corrupt_stored_evidence(tmp_path):
    from src.services.editing_state_service import EditorIdentity, EditingStateError
    path = tmp_path / "synthetic.xlsx"
    path.write_bytes(b"synthetic")
    service = EditingStateService(tmp_path / "editing")
    owner = EditorIdentity("synthetic", "Synthetic")
    lease = service.acquire_lease(path, owner, "tab")
    before = stored_evidence(service, lease["document_id"])
    with pytest.raises(EditingStateError) as error:
        service.save_autosave(document_id=lease["document_id"], identity=owner, client_id="tab",
                              lease_token=lease["lease"]["token"], base_revision=lease["revision"],
                              idempotency_key=" ", document={"intervention_report": "Synthetic"})
    assert not isinstance(error.value, EditingStateQuarantinedError)
    assert stored_evidence(service, lease["document_id"]) == before


def test_admin_lists_corrupt_document_as_requiring_recovery(quarantine_app):
    _, service, document_id, _ = quarantine_app
    before = stored_evidence(service, document_id)
    records = service.admin_documents()
    assert len(records) == 1
    assert records[0]["document_id"] == document_id
    assert records[0]["quarantined"] is True
    assert records[0]["code"] == "editing_state_quarantined"
    assert "lock_version" not in records[0]
    assert stored_evidence(service, document_id) == before


@pytest.mark.parametrize("changes", [
    {"version": 99}, {"version": True}, {"document_id": "different-document"},
    {"revision": "invalid"}, {"sessions": []}, {"sessions": {"token": []}},
    {"operations": []}, {"operations": {"send": {"context": []}}},
    {"autosave": []}, {"owner": False}, {"admin_blocked_users": {"user": None}},
])
def test_invalid_state_is_quarantined_before_any_normalization(tmp_path, changes):
    service = EditingStateService(tmp_path / "editing")
    service.snapshot_document("broken-document")
    state = json.loads(stored_evidence(service, "broken-document")[0])
    state.update(changes)
    replace_stored_state(service, "broken-document", json.dumps(state))
    before = stored_evidence(service, "broken-document")

    with pytest.raises(EditingStateQuarantinedError):
        service.snapshot_document("broken-document")
    assert service.release_user_leases("unrelated-user") == 0

    assert stored_evidence(service, "broken-document") == before


@pytest.mark.parametrize("changes", [{"version": 99}, {"document_id": "other"}, {"sessions": []}])
def test_invalid_editing_evidence_never_releases_a_held_external_job(tmp_path, changes):
    from src.services.editing_state_service import EditorIdentity
    from src.services.graph_sync_queue import GraphSyncQueue
    path = tmp_path / "synthetic.xlsx"
    path.write_bytes(b"synthetic")
    service = EditingStateService(tmp_path / "editing")
    owner = EditorIdentity("user", "Synthetic")
    lease = service.acquire_lease(path, owner, "tab")
    doc = lease["document_id"]
    service.claim_operation(document_id=doc, kind="send", idempotency_key="send",
                            identity=owner, client_id="tab", lease_token=lease["lease"]["token"],
                            base_revision=lease["revision"])
    service.commit_operation(document_id=doc, idempotency_key="send", path=None, response={"success": True})
    state = json.loads(stored_evidence(service, doc)[0])
    state.update(changes)
    replace_stored_state(service, doc, json.dumps(state))
    before = stored_evidence(service, doc)
    calls = []

    class Graph:
        def remove_active_name(self, *args, **kwargs):
            calls.append("delete")
            return True

    queue = GraphSyncQueue(Graph(), tmp_path / "queue.sqlite3", auto_start=False)
    job = queue.enqueue("remove_active", {"source_name": "synthetic", "_commit_guard": {
        "database": str(service.repository.database_path), "document_id": doc, "operation_id": "send"}})
    queue.run_until_idle()
    assert calls == []
    assert queue.status(job["id"])["status"] == "held"
    assert stored_evidence(service, doc) == before


@pytest.mark.parametrize("fault", ["duplicate-version", "huge-timestamp", "missing-operations", "not-an-object"])
def test_ambiguous_or_unusable_evidence_is_preserved(tmp_path, fault):
    service = EditingStateService(tmp_path / "editing")
    service.snapshot_document("document")
    raw = stored_evidence(service, "document")[0]
    if fault == "duplicate-version":
        raw = '{"version":99,' + raw[1:]
    elif fault == "not-an-object":
        raw = '[]'
    else:
        state = json.loads(raw)
        if fault == "huge-timestamp":
            state["source_changed_at"] = 10 ** 1000
        else:
            state.pop("operations")
        raw = json.dumps(state)
    replace_stored_state(service, "document", raw)
    before = stored_evidence(service, "document")
    with pytest.raises(EditingStateQuarantinedError):
        service.snapshot_document("document")
    assert service.release_user_leases("unrelated") == 0
    assert stored_evidence(service, "document") == before


def test_logout_http_preserves_other_autosave_and_admin_revocation(quarantine_app):
    from types import SimpleNamespace
    from src.services.editing_state_service import EditorIdentity, EditingSessionRevokedError
    app, service, document_id, path = quarantine_app
    original = path.parent.parent / "synthetic.xlsx"
    owner = EditorIdentity("other-user", "Synthetic other")
    lease = service.acquire_lease(original, owner, "other-tab")
    doc = lease["document_id"]
    service.save_autosave(document_id=doc, identity=owner, client_id="other-tab",
                          lease_token=lease["lease"]["token"], base_revision=lease["revision"],
                          idempotency_key="save", document={"intervention_report": "Preservar"})
    admin = SimpleNamespace(id="admin", email="jribeiro@sensorpoint.pt")
    record = next(row for row in service.admin_documents() if row["document_id"] == doc)
    service.admin_change_access(doc, admin, "Intervenção sintética", record["lock_version"])
    before_bad = stored_evidence(service, document_id)
    response = app.test_client().post("/logout")
    assert response.status_code == 302
    assert service.snapshot_document(doc)["server_document"] == {"intervention_report": "Preservar"}
    with pytest.raises(EditingSessionRevokedError):
        service.acquire_lease(original, owner, "new-tab")
    assert stored_evidence(service, document_id) == before_bad


def test_preview_of_client_payload_does_not_read_or_replace_quarantined_state(quarantine_app):
    app, service, document_id, path = quarantine_app
    app.config["MAINTENANCE_ENABLED"] = True
    app.config["MAINTENANCE_DEMO"] = False
    before = stored_evidence(service, document_id)
    response = app.test_client().post(f"/api/file/{path.stem}/document-preview", json={"intervention_report": "Synthetic"})
    assert response.status_code == 200 and response.json["success"] is True
    assert stored_evidence(service, document_id) == before


def test_invalid_initial_state_is_not_reported_as_corrupt_stored_evidence(tmp_path):
    from src.services.editing_state_service import EditingStateError
    service = EditingStateService(tmp_path / "editing")
    with pytest.raises(EditingStateError) as error:
        service.snapshot_document(" ")
    assert not isinstance(error.value, EditingStateQuarantinedError)
    assert service.repository.document_ids() == []


@pytest.mark.parametrize("shape", ["v1", "lease", "incomplete-v2"])
def test_historical_shapes_require_explicit_preparation_and_remain_intact(tmp_path, shape):
    from tests.legacy_editing_shapes import version_one, legacy_lease
    service = EditingStateService(tmp_path / "editing")
    service.snapshot_document("historical")
    state = legacy_lease("historical") if shape == "lease" else version_one("historical")
    if shape == "incomplete-v2":
        state["version"] = 2
    replace_stored_state(service, "historical", json.dumps(state))
    before = stored_evidence(service, "historical")
    with pytest.raises(EditingStateQuarantinedError):
        service.snapshot_document("historical")
    assert list(service.finalizations_to_retire()) == []
    assert service.admin_documents()[0]["quarantined"] is True
    assert service.release_user_leases("unrelated") == 0
    assert stored_evidence(service, "historical") == before


def test_quarantine_diagnostic_emits_only_safe_reference(quarantine_app, caplog):
    from src.logging_config import JsonLogFormatter
    _, service, document_id, _ = quarantine_app
    service.admin_documents()
    records = [record for record in caplog.records if getattr(record, "event", "") == "editing_state_quarantined"]
    assert records
    encoded = JsonLogFormatter().format(records[-1])
    payload = json.loads(encoded)
    assert len(payload["document_ref"]) == 16
    assert document_id not in encoded and "state_json" not in encoded
