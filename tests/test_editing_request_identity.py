"""HTTP identity must protect the same document in the guard and in the write."""
import json

import pytest

from src.services.editing_state_service import EditorIdentity
from tests.test_editing_quarantine import quarantine_app, replace_stored_state, stored_evidence


@pytest.fixture
def valid_request(quarantine_app):
    app, service, document_id, path = quarantine_app
    raw = app.config["SYNTHETIC_INITIAL_STATE"]
    replace_stored_state(service, document_id, raw)
    state = json.loads(raw)
    metadata = {"document_id": document_id, "client_id": "tab-1",
                "lease_token": next(iter(state["sessions"])), "base_revision": state["revision"],
                "idempotency_key": "synthetic-identity-check"}
    return app.test_client(), service, document_id, path, metadata


REQUESTS = [("draft", "json"), ("draft", "multipart"), ("send", "json"),
            ("send", "multipart"), ("cancel", "json")]


def post(client, path, route, encoding, metadata):
    payload = {"_edit": metadata, "intervention_report": "Synthetic identity check"}
    url = f"/api/file/{path.stem}/{route}"
    if encoding == "multipart":
        return client.post(url, data={"document": json.dumps(payload)}, content_type="multipart/form-data")
    return client.post(url, json=payload)


@pytest.mark.parametrize("route,encoding", REQUESTS)
@pytest.mark.parametrize("field", ["document_id", "client_id", "lease_token", "idempotency_key"])
def test_padded_identifiers_are_rejected_before_any_guard_or_write(valid_request, route, encoding, field):
    client, service, document_id, path, metadata = valid_request
    before = stored_evidence(service, document_id)
    contents = path.read_bytes()
    metadata[field] = " " + metadata[field] + " "
    response = post(client, path, route, encoding, metadata)
    assert response.status_code == 409 and response.json["code"] == "editing_session_required"
    assert service.repository.document_ids() == [document_id]
    assert stored_evidence(service, document_id) == before
    assert path.read_bytes() == contents


@pytest.mark.parametrize("route,encoding", REQUESTS)
def test_real_document_guard_blocks_all_exclusive_http_writes(valid_request, route, encoding):
    client, service, document_id, path, metadata = valid_request
    contents = path.read_bytes()
    with service.operation_guard(document_id, EditorIdentity("synthetic-user", "Synthetic")):
        response = post(client, path, route, encoding, metadata)
    assert response.status_code == 409 and response.json["code"] == "operation_in_progress"
    assert service.repository.document_ids() == [document_id]
    assert json.loads(stored_evidence(service, document_id)[0])["operations"] == {}
    assert path.read_bytes() == contents


@pytest.mark.parametrize("encoding", ["json", "multipart"])
def test_http_guard_prevents_admin_release_before_claiming_operation(valid_request, monkeypatch, encoding):
    from src.services import admin_access
    from src.services.photo_attachment_service import PhotoAttachmentService
    from src.services.editing_state_service import OperationInProgressError
    from tests.test_operational_admin import ADMIN
    # Match the stable synthetic identity required by the current admin policy.
    monkeypatch.setattr(admin_access, "ADMIN_TENANT_ID", ADMIN.tenant_id)
    monkeypatch.setattr(admin_access, "ADMIN_OBJECT_ID", ADMIN.id)
    client, service, document_id, path, metadata = valid_request
    original = PhotoAttachmentService.prepare_uploads
    attempts = []

    def prepare_while_admin_tries(uploads):
        # Exercise the public admin service inside the HTTP view, before the
        # pending-operation flag exists. Only the real document mutex protects it.
        assert json.loads(stored_evidence(service, document_id)[0])["operations"] == {}
        version = service.admin_documents()[0]["lock_version"]
        with pytest.raises(OperationInProgressError):
            service.admin_change_access(document_id, ADMIN, "Verificação sintética", version)
        attempts.append(True)
        return original(uploads)

    monkeypatch.setattr(PhotoAttachmentService, "prepare_uploads", prepare_while_admin_tries)
    response = post(client, path, "draft", encoding, metadata)
    assert response.status_code == 200 and response.json["success"] is True
    assert attempts == [True] and not service.repository.admin_history()
    assert service.snapshot_document(document_id)["owner"]["owner_id"] == "synthetic-user"
