"""The SADI trial never makes checklist completeness a service-sheet prerequisite."""
from copy import deepcopy

import pytest

from src import maintenance_schema as maintenance
from src.services.document_data_service import DocumentDataService
from src.services.editing_state_service import EditorIdentity
from src.services.maintenance_private_service import MaintenancePrivateService
from tests.test_maintenance import complete_document, sign_document
from tests.test_maintenance_access import production_sadi, login, lease


@pytest.mark.parametrize("email", ["outro@sensorpoint.pt", "jribeiro@sensorpoint.pt", "acarvalho@sensorpoint.pt"])
@pytest.mark.parametrize("state", ["absent", "empty", "partial", "unsigned", "stale", "invalid_photo"])
def test_optional_checklists_preserved_without_final_pdfs_and_send_replays(production_sadi, email, state):
    app, client, draft, edits, root = production_sadi
    login(client, email)
    metadata = lease(client, draft, "optional-sadi")
    document = complete_document()
    if state == "absent":
        document.pop("maintenance_checklists")
    elif state == "empty":
        document["maintenance_checklists"] = []
    elif state == "partial":
        document["maintenance_checklists"][0]["location"] = ""
    elif state == "stale":
        sign_document(document, app.secret_key)
        document["maintenance_checklists"][0]["final_observations"] = "Mudança depois da assinatura"
    elif state == "invalid_photo":
        document["maintenance_checklists"][0]["photos"] = [{"id": "photo", "image": "https://example.invalid/never-fetch.jpg"}]
    expected = deepcopy(document)
    expected["maintenance_checklists"] = maintenance.normalize_sites(document.get("maintenance_checklists", []))
    maintenance.clear_invalid_signatures(expected, app.secret_key)
    private = MaintenancePrivateService(root / "private/sadi")
    response = client.post(f"/api/file/{draft.stem}/send", json={**document, "_edit": metadata})
    assert response.status_code == 200, response.json
    assert not response.json.get("maintenance_bundle_url")
    assert private.read(metadata["document_id"]) == expected["maintenance_checklists"]
    assert not list((root / "private/sadi").rglob("*.pdf"))
    archive = next((root / "archive").rglob("*.xlsx"))
    assert "maintenance_checklists" not in DocumentDataService(archive).read()
    assert not list((root / "archive").rglob("*__SADI_*"))
    queue = app.extensions["test_graph_queue"]
    queue.run_until_idle()
    assert queue.status(response.json["graph_job_id"])["status"] == "complete"
    repeated = client.post(f"/api/file/{draft.stem}/send", json={**document, "_edit": metadata})
    assert repeated.status_code == 200 and repeated.json == response.json
    queue.run_until_idle()
    assert [kind for kind, _ in queue.graph_service.calls] == ["upload_archive", "remove_active"]


@pytest.mark.parametrize("email", ["outro@sensorpoint.pt", "jribeiro@sensorpoint.pt"])
def test_optional_checklists_do_not_waive_service_sheet_requirements(production_sadi, email):
    app, client, draft, _edits, root = production_sadi
    login(client, email)
    metadata = lease(client, draft, "sheet-required")
    document = complete_document()
    document["maintenance_checklists"] = []
    document["technician_records"] = []
    document["client_not_present"] = False
    response = client.post(f"/api/file/{draft.stem}/send", json={**document, "_edit": metadata})
    assert response.status_code == 400
    assert response.json["missing_fields"]
    assert all("SADI" not in error for error in response.json["missing_fields"])
    assert draft.exists() and not list((root / "archive").rglob("*.xlsx"))
    assert app.extensions["test_graph_queue"].graph_service.calls == []


def test_send_preserves_reopened_autosave_checklists_for_newly_allowed_editor(production_sadi):
    app, client, draft, edits, root = production_sadi
    email = "outro@sensorpoint.pt"
    login(client, email)
    metadata = lease(client, draft, "autosave-only")
    document = complete_document()
    document["maintenance_checklists"][0]["final_observations"] = "Somente no autosave do servidor"
    saved = edits.save_autosave(document_id=metadata["document_id"],
        identity=EditorIdentity(email, "Pessoa fictícia"), client_id=metadata["client_id"],
        lease_token=metadata["lease_token"], base_revision=metadata["base_revision"],
        document=document, idempotency_key="seed-autosave-only")
    metadata["base_revision"] = saved["revision"]
    reopened = client.get(f"/api/file/{draft.stem}/bootstrap?client_id=autosave-only")
    assert reopened.status_code == 200
    document = reopened.json["document"]
    assert document["maintenance_checklists"][0]["final_observations"] == "Somente no autosave do servidor"
    response = client.post(f"/api/file/{draft.stem}/send", json={**document, "_edit": metadata})
    assert response.status_code == 200, response.json
    assert MaintenancePrivateService(root / "private/sadi").read(metadata["document_id"]) == document["maintenance_checklists"]


def test_private_preservation_failure_keeps_draft_and_no_archive_job(production_sadi, monkeypatch):
    app, client, draft, _edits, root = production_sadi
    login(client, "jribeiro@sensorpoint.pt")
    metadata = lease(client, draft, "private-write-failure")
    document = complete_document()  # unsigned: no PDF render, but data must survive
    before = draft.read_bytes()
    def fail(*_args):
        raise OSError("Synthetic private write denied")
    monkeypatch.setattr(MaintenancePrivateService, "write", fail)
    response = client.post(f"/api/file/{draft.stem}/send", json={**document, "_edit": metadata})
    assert response.status_code == 500 and draft.read_bytes() == before
    assert not list((root / "archive").rglob("*.xlsx"))
    with app.extensions["test_graph_queue"]._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM graph_sync_jobs").fetchone()[0] == 0


@pytest.mark.parametrize("sadi", [False, True])
def test_no_checklist_data_needs_no_private_write(production_sadi, monkeypatch, sadi):
    app, client, draft, _edits, root = production_sadi
    login(client, "outro@sensorpoint.pt")
    metadata = lease(client, draft, "no-private-write")
    document = complete_document()
    document["maintenance_checklists"] = []
    document["equipments"]["sadi"] = sadi
    def fail(*_args):
        raise AssertionError("An unused SADI store must not be written")
    monkeypatch.setattr(MaintenancePrivateService, "write", fail)
    response = client.post(f"/api/file/{draft.stem}/send", json={**document, "_edit": metadata})
    assert response.status_code == 200, response.json
    assert not (root / "private/sadi").exists()
    assert response.json["graph_job_id"]


@pytest.mark.parametrize("sadi", [False, True])
@pytest.mark.parametrize("email", ["jribeiro@sensorpoint.pt", "outro@sensorpoint.pt"])
def test_authorized_explicit_clear_is_preserved_even_when_sadi_unchecked(production_sadi, sadi, email):
    _app, client, draft, _edits, root = production_sadi
    login(client, email)
    metadata = lease(client, draft, "explicit-clear")
    document = complete_document()
    private = MaintenancePrivateService(root / "private/sadi")
    private.write(metadata["document_id"], document["maintenance_checklists"])
    document["maintenance_checklists"] = []
    document["equipments"]["sadi"] = sadi
    response = client.post(f"/api/file/{draft.stem}/send", json={**document, "_edit": metadata})
    assert response.status_code == 200, response.json
    assert private.read(metadata["document_id"]) == []
    assert not list((root / "private/sadi").rglob("*.pdf"))


def test_newly_allowed_editor_complete_bundle_is_accessible_and_idempotent(production_sadi):
    app, client, draft, _edits, root = production_sadi
    login(client, "outro@sensorpoint.pt")
    metadata = lease(client, draft, "private-complete")
    document = complete_document()
    sign_document(document, app.secret_key)
    private = MaintenancePrivateService(root / "private/sadi")
    private.write(metadata["document_id"], document["maintenance_checklists"])
    response = client.post(f"/api/file/{draft.stem}/send", json={**document, "_edit": metadata})
    assert response.status_code == 200 and response.json["maintenance_bundle_url"]
    pdfs = {path: path.read_bytes() for path in (root / "private/sadi").rglob("*.pdf")}
    assert len(pdfs) == 3
    key = next((root / "private/sadi/pdf").iterdir()).name
    assert client.get(f"/maintenance/bundles/{key}").status_code == 200
    repeated = client.post(f"/api/file/{draft.stem}/send", json={**document, "_edit": metadata})
    assert repeated.status_code == 200 and repeated.json == response.json
    assert {path: path.read_bytes() for path in (root / "private/sadi").rglob("*.pdf")} == pdfs
