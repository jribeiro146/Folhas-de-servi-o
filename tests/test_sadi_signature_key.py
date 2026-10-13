"""Key continuity, warning persistence and archived SADI outputs; all fixtures are synthetic."""
from copy import deepcopy
import uuid
import pytest

from src import maintenance_schema as m
from src.document_schema import normalize_document_payload
from src.services.document_data_service import DocumentDataService
from tests.test_maintenance import complete_document, sign_document, signature_image
from tests.test_maintenance_access import production_sadi, login, lease


def test_same_key_preserves_historical_signatures_without_warning_metadata():
    document = complete_document()
    for site in document["maintenance_checklists"]:
        site.pop("signature_warnings", None)
    sign_document(document, "synthetic-original-key")
    original = deepcopy(document)
    loaded = normalize_document_payload(document)
    m.clear_invalid_signatures(loaded, "synthetic-original-key")
    for before, after in zip(original["maintenance_checklists"], loaded["maintenance_checklists"]):
        assert after["signatures"] == before["signatures"]
        assert after["signature_warnings"] == {}
    assert m.document_errors(loaded, "synthetic-original-key") == []


def test_changed_key_removes_invalid_proof_and_preserves_warning_across_draft_round_trips():
    document = complete_document()
    sign_document(document, "synthetic-original-key")
    first = document["maintenance_checklists"][0]
    first["signature_drafts"]["technician"] = deepcopy(first["signatures"]["technician"])
    m.clear_invalid_signatures(document, "synthetic-replacement-key")
    for _ in range(3):
        document = normalize_document_payload(document)
        m.clear_invalid_signatures(document, "synthetic-replacement-key")
        for site in document["maintenance_checklists"]:
            assert site["signatures"] == {}
            assert site["signature_warnings"] == {"technician": "verification_failed", "customer": "verification_failed"}
        assert not document["maintenance_checklists"][0]["signature_drafts"].get("technician")
    errors = m.document_errors(document, "synthetic-replacement-key")
    assert len(errors) == 4
    assert all("chave de segurança" in error and "nova assinatura" in error for error in errors)


def test_recollecting_one_role_clears_only_its_warning_without_changing_other_signature_content():
    document = complete_document()
    sign_document(document, "synthetic-original-key")
    m.clear_invalid_signatures(document, "synthetic-replacement-key")
    site = document["maintenance_checklists"][0]
    for role in ("technician", "customer"):
        site["signatures"][role] = m.make_signature(
            "synthetic-replacement-key", document, site, role,
            "Pessoa fictícia", "2026-10-13", signature_image())
        m.clear_invalid_signatures(document, "synthetic-replacement-key")
        assert role not in site["signature_warnings"]
        assert m.signature_valid("synthetic-replacement-key", document, site, "technician")
    assert site["signature_warnings"] == {}
    assert document["maintenance_checklists"][1]["signature_warnings"] == {
        "technician": "verification_failed", "customer": "verification_failed"}


@pytest.mark.parametrize("token", [None, "", "x" * 64, "assinatura-🔐", ["malformed"]])
def test_malformed_signature_is_rejected_with_a_warning_instead_of_crashing(token):
    document = complete_document()
    sign_document(document, "synthetic-key")
    site = document["maintenance_checklists"][0]
    site["signatures"]["technician"]["token"] = token
    m.clear_invalid_signatures(document, "synthetic-key")
    assert "technician" not in site["signatures"]
    assert site["signature_warnings"] == {"technician": "verification_failed"}
    assert m.signature_valid("synthetic-key", document, site, "customer")


def test_warnings_are_bounded_and_do_not_waive_or_invalidate_valid_signatures():
    document = complete_document()
    sign_document(document, "synthetic-key")
    site = document["maintenance_checklists"][0]
    site["signature_warnings"] = {"technician": "verification_failed", "customer": "invented", "admin": "private"}
    normalized = normalize_document_payload(document)
    assert normalized["maintenance_checklists"][0]["signature_warnings"] == {"technician": "verification_failed"}
    assert m.document_errors(normalized, "synthetic-key") == []
    m.clear_invalid_signatures(normalized, "synthetic-key")
    assert normalized["maintenance_checklists"][0]["signature_warnings"] == {}
    site["customer_not_present"] = True
    site["signature_warnings"]["customer"] = "verification_failed"
    site = m.normalize_sites([site])[0]
    assert "customer" not in site["signature_warnings"]
    assert "customer" not in site["signatures"]
    assert "technician" in site["signature_warnings"]


def metadata_from(client, draft, metadata):
    state = client.get(f"/api/file/{draft.stem}").json["editing"]
    return {**metadata, "base_revision": state["revision"],
            "idempotency_key": uuid.uuid4().hex}


def test_http_rotation_warns_on_reopen_and_validation_then_requires_recollection(production_sadi):
    app, client, draft, _, root = production_sadi
    login(client, "jribeiro@sensorpoint.pt")
    metadata = lease(client, draft, "synthetic-m22-rotation")
    document = complete_document()
    sign_document(document, app.secret_key)
    saved = client.post(f"/api/file/{draft.stem}/draft", json={**document, "_edit": metadata})
    assert saved.status_code == 200, saved.json
    app.extensions["test_graph_queue"].run_until_idle()
    before = deepcopy(DocumentDataService(draft).read())
    private_before = {path: path.read_bytes() for path in (root / "private" / "sadi").rglob("*.json")}
    assert private_before
    app.secret_key = "synthetic-replacement-key-for-m22-only"
    assert client.get(f"/api/file/{draft.stem}").status_code == 401
    login(client, "jribeiro@sensorpoint.pt")
    opened = client.get(f"/api/file/{draft.stem}")
    assert opened.status_code == 200
    loaded = opened.json["document"]
    assert all(site["signature_warnings"] for site in loaded["maintenance_checklists"])
    # A read does not rewrite the stored signature. A subsequent explicit save
    # carries the warning, never silently signs the old image with a new key.
    assert DocumentDataService(draft).read() == before
    assert {path: path.read_bytes() for path in private_before} == private_before
    checked = client.post(f"/api/file/{draft.stem}/maintenance/validate", json={"document": document}).json
    assert all("chave de segurança" in error for error in checked["errors"])
    assert all(not any(site["signed"].values()) for site in checked["sites"])
    assert all(not site["signatures"] and site["signature_warnings"] for site in checked["maintenance_checklists"])
    metadata = metadata_from(client, draft, metadata)
    autosaved = client.post(f"/api/file/{draft.stem}/autosave", json={"document": document, "_edit": metadata})
    assert autosaved.status_code == 200, autosaved.json
    assert all(not site["signatures"] and site["signature_warnings"]
               for site in autosaved.json["editing"]["server_document"]["maintenance_checklists"])
    assert {path: path.read_bytes() for path in private_before} == private_before
    assert not list((root / "private" / "sadi").rglob("*.pdf"))
    reopened = client.get(f"/api/file/{draft.stem}").json["document"]
    assert all(not site["signatures"] and site["signature_warnings"] for site in reopened["maintenance_checklists"])
    metadata = metadata_from(client, draft, metadata)
    blocked = client.post(f"/api/file/{draft.stem}/send", json={**document, "_edit": metadata})
    assert blocked.status_code == 400, blocked.json
    assert len(blocked.json["missing_fields"]) == 4
    assert all("chave de segurança" in error for error in blocked.json["missing_fields"])
    assert draft.exists() and not list((root / "private" / "sadi").rglob("*.pdf"))
    metadata = metadata_from(client, draft, metadata)
    saved = client.post(f"/api/file/{draft.stem}/draft", json={**loaded, "_edit": metadata})
    assert saved.status_code == 200, saved.json
    loaded = client.get(f"/api/file/{draft.stem}").json["document"]
    assert all(site["signature_warnings"] for site in loaded["maintenance_checklists"])
    metadata = metadata_from(client, draft, metadata)
    for site in loaded["maintenance_checklists"]:
        for role in ("technician", "customer"):
            result = client.post(f"/api/file/{draft.stem}/maintenance/sign", json={
                "_edit": metadata, "document": loaded, "site_id": site["id"], "role": role,
                "name": "Pessoa fictícia", "date": "2026-10-13", "image": signature_image()})
            assert result.status_code == 200, result.json
            site["signatures"][role] = result.json["signature"]
    saved = client.post(f"/api/file/{draft.stem}/draft", json={**loaded, "_edit": metadata})
    assert saved.status_code == 200, saved.json
    reloaded = client.get(f"/api/file/{draft.stem}").json["document"]
    assert all(site["signature_warnings"] == {} for site in reloaded["maintenance_checklists"])
    assert m.document_errors(reloaded, app.secret_key) == []


def test_key_change_expires_login_but_keeps_finalized_private_pdf_bytes(production_sadi):
    app, client, draft, _, _ = production_sadi
    login(client, "jribeiro@sensorpoint.pt")
    metadata = lease(client, draft, "synthetic-m22-pdf")
    document = complete_document()
    sign_document(document, app.secret_key)
    finished = client.post(f"/api/file/{draft.stem}/send", json={**document, "_edit": metadata})
    assert finished.status_code == 200, finished.json
    bundle = finished.json["maintenance_bundle_url"]
    import re
    html = client.get(bundle).text
    pdf_urls = re.findall(r'href="([^"]+/pdf/[^"?]+)"', html)
    assert pdf_urls
    before = {url: client.get(url).data for url in pdf_urls}
    app.secret_key = "synthetic-new-key-for-existing-pdfs"
    assert client.get("/api/files").status_code == 401
    login(client, "jribeiro@sensorpoint.pt")
    for url, content in before.items():
        result = client.get(url)
        assert result.status_code == 200
        assert result.data == content and content.startswith(b"%PDF-")
        assert "no-store" in result.headers["Cache-Control"]
    login(client, "outro@sensorpoint.pt")
    assert all(client.get(url).status_code == 404 for url in pdf_urls)
