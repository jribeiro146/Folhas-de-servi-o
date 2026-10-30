"""Synthetic recent activity: durable attribution, truthful outcomes and privacy."""
import json
import pytest

from src.services.application_activity import recent_activity
from src.services.editing_state_service import EditingStateService, EditorIdentity
from src.services.graph_sync_queue import GraphSyncQueue
from tests.test_operational_admin import (ADMIN, admin_app, configured_synthetic_admin, document, login)
from tests.test_editing_request_identity import valid_request, quarantine_app


def seed(service, identifier="synthetic", *, kind="send", status="complete", when=100, metadata=True, remote=False):
    with service._state_transaction(identifier) as state:
        state["aliases"] = ["2026_9901_Ficha_ficticia"]
        state["operations"]["request"] = {
            "kind": kind, "status": status, "owner_id": "private-owner-id", "base_revision": 1,
            "created_at": when, "updated_at": when, "context": {"private": "SECRET-CONTEXT"},
            "response": {"graph_job_id": f"{kind}:{identifier}:request" if remote else None,
                         "private": "SECRET-RESPONSE"},
            **({"activity": {"actor_name": "Ana Fictícia", "sheet_name": "2026_9901_Ficha_ficticia"}} if metadata else {}),
        }
    return f"{kind}:{identifier}:request"


def test_claim_stores_authenticated_author_and_replay_does_not_duplicate(tmp_path):
    service, file, editor, snapshot = document(tmp_path)
    arguments = dict(document_id=snapshot["document_id"], kind="draft", idempotency_key="request",
                     identity=editor, client_id="tab-1", lease_token=snapshot["lease"]["token"],
                     base_revision=snapshot["revision"])
    service.claim_operation(**arguments)
    service.commit_operation(document_id=snapshot["document_id"], idempotency_key="request", path=file, response={})
    assert service.claim_operation(**arguments)["status"] == "replay"
    entries = recent_activity(service.repository)["entries"]
    assert len(entries) == 1 and entries[0]["actor_name"] == editor.display_name
    assert entries[0]["status"] == "complete" and entries[0]["mail_status"] == "not_recorded"


def test_retry_keeps_initial_author_and_legacy_retry_does_not_invent_one(tmp_path):
    service, _, editor, snapshot = document(tmp_path)
    arguments = dict(document_id=snapshot["document_id"], kind="draft", idempotency_key="request",
                     identity=editor, client_id="tab-1", lease_token=snapshot["lease"]["token"],
                     base_revision=snapshot["revision"])
    service.claim_operation(**arguments)
    for legacy in (False, True):
        with service._state_transaction(snapshot["document_id"]) as state:
            operation = state["operations"]["request"]
            operation["status"] = "failed"
            if legacy:
                operation.pop("activity")
        service.claim_operation(**{**arguments, "identity": EditorIdentity(editor.id, "Nome alterado")})
        entry = recent_activity(service.repository)["entries"][0]
        assert entry["actor_name"] == ("Autor não registado" if legacy else editor.display_name)


def test_account_without_display_name_remains_attributable(tmp_path):
    service, _, editor, snapshot = document(tmp_path)
    service.claim_operation(document_id=snapshot["document_id"], kind="draft", idempotency_key="account-only",
        identity=EditorIdentity(editor.id, "\u202eana@example.invalid\n"), client_id="tab-1",
        lease_token=snapshot["lease"]["token"], base_revision=snapshot["revision"])
    assert recent_activity(service.repository)["entries"][0]["actor_name"] == "ana@example.invalid"


@pytest.mark.parametrize("result,uncertain", [
    ({"cancellation_started": True, "phase": "removal_unknown"}, True),
    ({"cancellation_started": True, "cancellation_confirmed": True}, False),
    ({"phase": "cancellation_rejected"}, False),
])
def test_cancel_history_distinguishes_uncertain_remote_effects(tmp_path, result, uncertain):
    service = EditingStateService(tmp_path / "edits")
    job_id = seed(service, kind="cancel", remote=True)
    queue = GraphSyncQueue(None, tmp_path / "queue.sqlite3", auto_start=False)
    queue.enqueue("remove_active", {}, job_id=job_id)
    with queue._connect() as connection:
        connection.execute("UPDATE graph_sync_jobs SET status='failed',result_json=? WHERE id=?", (json.dumps(result), job_id))
    assert recent_activity(service.repository, queue)["entries"][0]["reconciliation_required"] is uncertain


def test_superseded_draft_is_not_presented_as_published_by_this_job(tmp_path):
    service = EditingStateService(tmp_path / "edits")
    job_id = seed(service, kind="draft", remote=True)
    queue = GraphSyncQueue(None, tmp_path / "queue.sqlite3", auto_start=False)
    draft = tmp_path / "synthetic-draft" / "synthetic-draft.xlsx"
    draft.parent.mkdir()
    draft.write_bytes(b"synthetic staged bytes; never published")
    queue.enqueue("upload_active", {"draft_path": str(draft)}, job_id=job_id)
    with queue._connect() as connection:
        connection.execute("UPDATE graph_sync_jobs SET status='complete',result_json=? WHERE id=?",
                           (json.dumps({"superseded_by": "newer-private-id"}), job_id))
    result = recent_activity(service.repository, queue)
    assert result["entries"][0]["queue_status"] == "superseded"
    assert "newer-private-id" not in json.dumps(result)


def test_existing_64_receipt_retention_remains_visible_and_unchanged(tmp_path):
    service = EditingStateService(tmp_path / "edits")
    seed(service)
    with service._state_transaction("synthetic") as state:
        template = state["operations"].pop("request")
        state["operations"] = {f"request-{i}": {**template, "created_at": i, "updated_at": i} for i in range(65)}
        service._prune_operations(state)
    result = recent_activity(service.repository)
    assert len(result["entries"]) == 64 and result["entries"][-1]["created_at"] == 1


def test_http_document_cannot_spoof_activity_author(valid_request):
    client, service, document_id, path, metadata = valid_request
    response = client.post(f"/api/file/{path.stem}/draft", json={"_edit": metadata,
        "intervention_report": "Fictício", "activity": {"actor_name": "Falso"},
        "owner_name": "Falso", "technician_records": []})
    assert response.status_code == 200
    entry = recent_activity(service.repository)["entries"][0]
    assert entry["actor_name"] == "Synthetic" and entry["actor_name"] != "Falso"


@pytest.mark.parametrize("status,result,attempts,expected", [
    ("pending", {}, 0, "pending"),
    ("failed", {"phase": "mail_rejected"}, 1, "rejected"),
    ("failed", {"phase": "mail_unknown"}, 1, "unknown"),
    ("running", {"phase": "sending_mail"}, 1, "unknown"),
    ("failed", {}, 1, "unknown"),
    ("complete", {}, 1, "unknown"),
    ("complete", {"mail": {"accepted": True, "to": "SECRET-RECIPIENT"}}, 1, "accepted"),
    ("failed", {"mail": {"accepted": True}, "phase": "teams_rejected"}, 1, "accepted"),
    ("complete", {"mail": {"accepted": True, "reconciled": True}}, 1, "accepted"),
    ("failed", {"mail": {"accepted": "true"}}, 1, "unknown"),
    ("failed", {"invalid_state": True, "mail": {"accepted": True}}, 1, "unknown"),
    ("failed", {"restore_uncertain": True, "mail": {"accepted": True}}, 1, "unknown"),
    ("failed", {"phase": "unrecognized"}, 1, "unknown"),
])
def test_email_outcomes_use_receipts_not_local_completion(tmp_path, status, result, attempts, expected):
    service = EditingStateService(tmp_path / "edits")
    job_id = seed(service, remote=True)
    queue = GraphSyncQueue(None, tmp_path / "queue.sqlite3", auto_start=False)
    queue.enqueue("archive_and_mail_local", {"mail": {"to": "SECRET-RECIPIENT"}}, job_id=job_id)
    with queue._connect() as connection:
        connection.execute("UPDATE graph_sync_jobs SET status=?,result_json=?,attempts=?,updated_at=150 WHERE id=?",
                           (status, json.dumps(result), attempts, job_id))
    data = recent_activity(service.repository, queue)
    entry = data["entries"][0]
    assert entry["mail_status"] == expected
    assert entry["updated_at"] == (100 if entry["queue_status"] == "unavailable" else 150)
    raw = json.dumps(data)
    assert "SECRET" not in raw and "private-owner-id" not in raw and "sent_at" not in raw


def test_missing_queue_does_not_report_email_as_sent(tmp_path):
    service = EditingStateService(tmp_path / "edits")
    seed(service, remote=True, metadata=False)
    entry = recent_activity(service.repository)["entries"][0]
    assert entry["queue_status"] == "unavailable" and entry["mail_status"] == "unknown"
    assert entry["actor_name"] == "Autor não registado"


@pytest.mark.parametrize("mail,expected_status,expected_mail,uncertain", [
    (None, "complete", "not_requested", False),
    ([], "unavailable", "unknown", True),
    ("", "unavailable", "unknown", True),
    (False, "unavailable", "unknown", True),
])
def test_graph_finalization_without_email_accepts_null_but_not_invalid_types(tmp_path, mail, expected_status, expected_mail, uncertain):
    service = EditingStateService(tmp_path / "edits")
    job_id = seed(service, remote=True)
    queue = GraphSyncQueue(None, tmp_path / "queue.sqlite3", auto_start=False)
    queue.enqueue("archive_and_remove", {}, job_id=job_id)
    with queue._connect() as connection:
        connection.execute("UPDATE graph_sync_jobs SET status='complete',result_json=? WHERE id=?",
                           (json.dumps({"mail": mail, "removed_active": True}), job_id))
    entry = recent_activity(service.repository, queue)["entries"][0]
    assert entry["queue_status"] == expected_status and entry["mail_status"] == expected_mail
    assert entry["reconciliation_required"] is uncertain


def test_filters_apply_before_limit_and_order_uses_request_time(tmp_path):
    service = EditingStateService(tmp_path / "edits")
    for index in range(110):
        seed(service, f"draft-{index}", kind="draft", when=200 + index)
    seed(service, "send-1", when=10)
    seed(service, "send-2", when=20)
    with service._state_transaction("send-1") as state:
        state["operations"]["request"]["updated_at"] = 9999
    all_data = recent_activity(service.repository)
    assert len(all_data["entries"]) == 100 and all_data["has_more"] is True
    sends = recent_activity(service.repository, kind="send")
    assert [e["created_at"] for e in sends["entries"]] == [20, 10] and not sends["has_more"]
    assert recent_activity(service.repository, kind="cancel")["entries"] == []


def test_corrupt_state_is_reported_and_never_repaired_or_exposed(tmp_path):
    service = EditingStateService(tmp_path / "edits")
    seed(service)
    with service.repository._connect() as connection:
        connection.execute("INSERT INTO editing_states VALUES(?,?,?)", ("bad", "SECRET-CORRUPT", 100))
        before = [tuple(row) for row in connection.execute("SELECT * FROM editing_states ORDER BY document_id")]
    data = recent_activity(service.repository)
    assert len(data["entries"]) == 1 and data["unavailable_documents"] == 1
    assert "SECRET" not in json.dumps(data)
    with service.repository._connect() as connection:
        assert [tuple(row) for row in connection.execute("SELECT * FROM editing_states ORDER BY document_id")] == before


def test_activity_endpoint_is_admin_only_read_only_and_not_cached(admin_app):
    app, service, _ = admin_app
    seed(service)
    client = login(app, ADMIN.email)
    response = client.get("/api/admin/activity?kind=send")
    assert response.status_code == 200 and "no-store" in response.headers["Cache-Control"]
    assert len(response.json["activity"]["entries"]) == 1
    assert client.get("/api/admin/activity?kind=invalid").status_code == 400
    assert client.post("/api/admin/activity", json={}, headers={"X-Admin-CSRF": "synthetic-csrf"}).status_code == 405
    assert login(app, "technician@example.invalid").get("/api/admin/activity").status_code == 403
    assert app.test_client().get("/api/admin/activity").status_code != 200
    assert not service.repository.admin_history()
