"""SADI access and private archive checks with fictional users and no network."""

import json
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from src.services.document_data_service import DocumentDataService
from src.services.editing_state_service import EditingStateService
from src.services.file_service import FileService
from src.services.local_pdf_service import LocalPdfService
from src.services.maintenance_private_service import MaintenancePrivateService
from src.services.graph_storage_service import GraphStorageService
from src.services.graph_sync_queue import GraphSyncQueue
from tests.test_maintenance import complete_document, sign_document, synthetic_workbook
import src.services.archive_service as archive_module
import src.web.application as web


@pytest.fixture
def production_sadi(tmp_path, monkeypatch, request):
    settings = getattr(request, "param", {})
    monkeypatch.setenv("FS_ENVIRONMENT", settings.get("mode", "production"))
    monkeypatch.delenv("FS_TEST_SYNTHETIC", raising=False)
    flag = settings.get("flag")
    if flag is None:
        monkeypatch.delenv("FS_MAINTENANCE_ENABLED", raising=False)
    else:
        monkeypatch.setenv("FS_MAINTENANCE_ENABLED", flag)
    authenticated = settings.get("auth", True)
    monkeypatch.setattr(web, "AUTH_ENABLED", authenticated)
    monkeypatch.setattr(web, "ACTIVE_AUTH_PROVIDER", "microsoft" if authenticated else "none")
    monkeypatch.setattr(web, "STORAGE_BACKEND", settings.get("backend", "graph"))
    monkeypatch.setattr(web, "APP_DATA_DIR", tmp_path / "private")
    monkeypatch.setattr(archive_module, "EXCEL_ARQUIVADAS_DIR", tmp_path / "archive")
    source = synthetic_workbook(tmp_path / "active" / "2026_9900.xlsx")
    files = FileService(source.parent)
    draft = files.create_draft_copy(source, "Técnico fictício")
    edits = EditingStateService(tmp_path / "editing")

    class Authentication:
        @staticmethod
        def user_from_session(payload):
            return SimpleNamespace(**payload) if payload else None

    class Graph:
        def __init__(self):
            self.calls = []

        @staticmethod
        def active_source_name(path):
            return path.parent.name

        @staticmethod
        def active_source_etag(_path):
            return "synthetic-etag"

        def upload_active_bundle(self, path, *, fail_if_exists=False):
            self.calls.append(("upload_active", path))
            return [path.name]

        def upload_archive_bundle(self, path):
            self.calls.append(("upload_archive", path))
            return [path.name]

        def remove_active_name(self, name, *, expected_etag=None):
            self.calls.append(("remove_active", name))
            return True

    class Refresh:
        @staticmethod
        def request_refresh(**_kwargs):
            return {"status": "idle"}

    def render(_html, destination):
        destination.write_bytes(b"%PDF-1.4\nsynthetic SADI access test")

    graph = Graph()
    queue = GraphSyncQueue(graph, tmp_path / "queue.sqlite3", auto_start=False)
    app = web.create_app(
        file_service=files,
        editing_state_service=edits,
        graph_service=graph,
        graph_sync_queue=queue,
        graph_refresh_coordinator=Refresh(),
        work_folder_service=object(),
        microsoft_auth_service=Authentication(),
        local_pdf_service=LocalPdfService(renderer=render),
    )
    app.extensions["test_graph_queue"] = queue
    assert not app.config["MAINTENANCE_DEMO"] and not app.config["SYNTHETIC_TEST_VERSION"]
    return app, app.test_client(), draft, edits, tmp_path


def login(client, email):
    with client.session_transaction() as session:
        session["microsoft_user"] = {
            "id": email, "display_name": "Pessoa fictícia", "email": email,
        }


def lease(client, draft, client_id):
    result = client.post(f"/api/file/{draft.stem}/lease", json={"client_id": client_id})
    assert result.status_code == 200, result.get_json()
    editing = result.get_json()["editing"]
    return {"document_id": editing["document_id"], "client_id": client_id,
            "lease_token": editing["lease"]["token"], "base_revision": editing["revision"],
            "idempotency_key": uuid.uuid4().hex}


@pytest.mark.parametrize("production_sadi,enabled", [
    ({}, True),
    ({"flag": "true"}, True),
    ({"flag": "false"}, False),
    ({"flag": ""}, False),
    ({"flag": "true", "mode": "development"}, False),
    ({"flag": "true", "mode": "test"}, False),
    ({"flag": "true", "auth": False}, False),
    ({"flag": "true", "backend": "local"}, False),
], indirect=["production_sadi"])
def test_production_sadi_default_preserves_auth_storage_and_explicit_disable(production_sadi, enabled):
    app, client, draft, _edits, _root = production_sadi
    assert app.config["MAINTENANCE_ENABLED"] is enabled
    login(client, "jribeiro@sensorpoint.pt")
    assert ("Checklists de manutenção" in client.get("/").get_data(as_text=True)) is enabled
    checked = client.post(f"/api/file/{draft.stem}/maintenance/validate", json={"document": complete_document()})
    assert checked.status_code == (200 if enabled else 404)


def test_only_named_microsoft_accounts_see_sadi_and_private_data_survives_other_editor(production_sadi):
    app, client, draft, edits, root = production_sadi
    document = complete_document()
    login(client, "outro@sensorpoint.pt")
    owner = lease(client, draft, "other")
    owner_draft = client.post(
        f"/api/file/{draft.stem}/draft",
        json={**document, "maintenance_checklists": [], "_edit": owner},
    )
    assert owner_draft.status_code == 200, owner_draft.get_json()

    login(client, " ACARVALHO@SENSORPOINT.PT ")
    allowed_html = client.get("/").get_data(as_text=True)
    assert "Checklists de manutenção" in allowed_html
    assert "graphEnabled: true" in allowed_html
    assert 'aria-live="polite" >A confirmar a lista do SharePoint' in allowed_html
    metadata = lease(client, draft, "allowed")
    sign_document(document, app.secret_key)
    saved = client.post(f"/api/file/{draft.stem}/draft", json={**document, "_edit": metadata})
    assert saved.status_code == 200, saved.get_json()
    document_id = edits.resolve_document_id(draft)
    private = MaintenancePrivateService(root / "private" / "sadi")
    assert len(private.read(document_id)) == 2
    assert "maintenance_checklists" not in DocumentDataService(draft).read()

    login(client, "outro@sensorpoint.pt")
    html = client.get("/").get_data(as_text=True)
    assert "Checklists de manutenção" not in html
    assert "B22" not in html
    response = client.get(f"/api/file/{draft.stem}")
    assert response.status_code == 200
    assert "maintenance_checklists" not in json.dumps(response.get_json())
    bootstrap = client.get(f"/api/file/{draft.stem}/bootstrap?client_id=other")
    assert bootstrap.status_code == 200
    assert "maintenance_checklists" not in json.dumps(bootstrap.get_json())
    assert client.post(f"/api/file/{draft.stem}/maintenance/validate", json={"document": document}).status_code == 404
    assert client.post(f"/api/file/{draft.stem}/maintenance/sign", json={"document": document}).status_code == 404
    assert client.post(f"/api/file/{draft.stem}/document-preview",
                       json={**document, "_maintenance_site_id": document["maintenance_checklists"][0]["id"]}).status_code == 404

    other = lease(client, draft, "other")
    hostile = {**document, "maintenance_checklists": []}
    hostile["customer_name"] = "Cliente fictício alterado"
    result = client.post(f"/api/file/{draft.stem}/draft", json={**hostile, "_edit": other})
    assert result.status_code == 200, result.get_json()
    assert len(private.read(document_id)) == 2
    assert "maintenance_checklists" not in DocumentDataService(draft).read()


def test_production_finalization_keeps_sadi_only_in_private_app_folder(production_sadi):
    app, client, draft, edits, root = production_sadi
    login(client, "jribeiro@sensorpoint.pt")
    metadata = lease(client, draft, "owner")
    document = complete_document()
    sign_document(document, app.secret_key)
    response = client.post(f"/api/file/{draft.stem}/send", json={**document, "_edit": metadata})
    assert response.status_code == 200, response.get_json()
    bundle_url = response.get_json()["maintenance_bundle_url"]
    assert bundle_url.startswith("/maintenance/bundles/")
    archive = next(path for path in (root / "archive").glob("*")
                   if path.is_dir() and list(path.glob("*.xlsx")))
    assert not list(archive.glob("*__SADI_*"))
    assert not (archive / "maintenance-manifest.json").exists()
    assert "maintenance_checklists" not in DocumentDataService(next(archive.glob("*.xlsx"))).read()
    assert all("__SADI_" not in item.name for item in GraphStorageService._iter_uploadable_files(archive))
    page = client.get(bundle_url)
    assert page.status_code == 200
    key = bundle_url.rsplit("/", 1)[-1]
    private = MaintenancePrivateService(root / "private" / "sadi")
    _, entries, directory = private.bundle(key)
    assert len(entries) == 3 and len(list(directory.glob("*.pdf"))) == 3
    if os.name != "nt":
        for folder in (private.root, private.root / "pdf", directory):
            assert folder.stat().st_mode & 0o777 == 0o700
        assert all(path.stat().st_mode & 0o777 == 0o600 for path in directory.iterdir())
    for entry in entries:
        assert client.get(bundle_url + "/pdf/" + entry["name"]).status_code == 200
    login(client, "outro@sensorpoint.pt")
    assert client.get(bundle_url).status_code == 404
    assert client.get(bundle_url + "/pdf/" + entries[1]["name"]).status_code == 404


def test_private_pdf_failure_preserves_draft_and_does_not_enqueue_graph(production_sadi):
    app, client, draft, _edits, root = production_sadi
    login(client, "jribeiro@sensorpoint.pt")
    metadata = lease(client, draft, "owner")
    document = complete_document()
    sign_document(document, app.secret_key)
    # The fixture's injected renderer is used by both the service sheet and SADI PDFs.
    from src.services import local_pdf_service as pdf_module
    original_export = pdf_module.LocalPdfService.export_html_pdf
    count = 0

    def fail_second(self, html, destination):
        nonlocal count
        count += 1
        if count == 2:
            raise pdf_module.LocalPdfError("Falha sintética no segundo PDF")
        return original_export(self, html, destination)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(pdf_module.LocalPdfService, "export_html_pdf", fail_second)
        response = client.post(f"/api/file/{draft.stem}/send",
                               json={**document, "_edit": metadata})
    assert response.status_code == 500
    assert draft.exists()
    assert not list((root / "archive").rglob("*.xlsx"))
    assert not list((root / "private" / "sadi" / "pdf").glob("[0-9a-f]*"))


def test_allowed_editor_cannot_take_unrelated_draft(production_sadi):
    _app, client, draft, _edits, _root = production_sadi
    login(client, "outro@sensorpoint.pt")
    owner = lease(client, draft, "owner")
    document = complete_document()
    document["service_types"]["manutencao"] = False
    saved = client.post(f"/api/file/{draft.stem}/draft",
                        json={**document, "maintenance_checklists": [], "_edit": owner})
    assert saved.status_code == 200, saved.get_json()
    login(client, "acarvalho@sensorpoint.pt")
    result = client.post(f"/api/file/{draft.stem}/lease", json={"client_id": "other"})
    assert result.status_code == 423


@pytest.mark.parametrize("sadi", [False, True])
def test_real_queue_blocks_finalization_until_draft_upload_finishes(production_sadi, sadi):
    app, client, draft, _edits, root = production_sadi
    queue = app.extensions["test_graph_queue"]
    login(client, "jribeiro@sensorpoint.pt")
    metadata = lease(client, draft, "owner")
    document = complete_document()
    document["equipments"]["sadi"] = sadi
    sign_document(document, app.secret_key)
    upload = queue.enqueue("upload_active", {"draft_path": str(draft)})
    before = draft.read_bytes()
    blocked = client.post(f"/api/file/{draft.stem}/send", json={**document, "_edit": metadata})
    assert blocked.status_code == 409, blocked.get_json()
    assert blocked.get_json()["code"] == "publication_pending"
    assert draft.read_bytes() == before and not list((root / "archive").rglob("*.xlsx"))
    assert queue.graph_service.calls == []
    queue.run_until_idle()  # Temporary SQLite queue and in-memory Graph only.
    assert queue.status(upload["id"])["status"] == "complete"
    finalized = client.post(f"/api/file/{draft.stem}/send", json={**document, "_edit": metadata})
    assert finalized.status_code == 200, finalized.get_json()
    job_id = finalized.get_json()["graph_job_id"]
    queue.run_until_idle()
    assert queue.status(job_id)["status"] == "complete"
    repeated = client.post(f"/api/file/{draft.stem}/send", json={**document, "_edit": metadata})
    assert repeated.status_code == 200 and repeated.get_json()["graph_job_id"] == job_id
    queue.run_until_idle()
    assert [kind for kind, _ in queue.graph_service.calls] == ["upload_active", "upload_archive", "remove_active"]


@pytest.mark.parametrize("email", [None, "outro@sensorpoint.pt", "jribeiro@sensorpoint.pt"])
@pytest.mark.parametrize("production_sadi", [{"flag": "true"}], indirect=True)
def test_public_manifest_with_sadi_active_in_production(production_sadi, email):
    app, client, *_ = production_sadi
    assert app.config["MAINTENANCE_ENABLED"]
    if email:
        login(client, email)
    response = client.get("/manifest.webmanifest")
    assert response.status_code == 200
    assert response.mimetype == "application/manifest+json"
    assert response.get_json()["start_url"] == "/"
    assert response.get_json()["icons"]
    cached = client.get("/manifest.webmanifest", headers={"If-None-Match": response.headers["ETag"]})
    assert cached.status_code == 304
    assert client.get("/service-worker.js").status_code == 200


def test_reads_private_checklist_written_by_1ede4d8_without_migration(production_sadi):
    _app, client, draft, edits, root = production_sadi
    fixture = Path(__file__).parent / "fixtures/sadi-private-1ede4d8.json"
    content = fixture.read_bytes()
    provenance = json.loads(fixture.with_suffix(".provenance.json").read_text(encoding="utf-8"))
    assert provenance["base_commit"] == "1ede4d8a690593a5f2b1d2908f3670fc1d038d7d"
    assert hashlib.sha256(content).hexdigest() == provenance["fixture_sha256"]
    expected = json.loads(content)
    assert expected[0]["conventional"][0]["checks"]["B44"]["answer"] == "OK"
    private = MaintenancePrivateService(root / "private" / "sadi")
    private.root.mkdir(parents=True, mode=0o700)
    document_id = edits.resolve_document_id(draft)
    # Copy the historical bytes directly to the original hashed filename.
    # Do not call the new writer or normalize/migrate the input.
    saved = private.root / (hashlib.sha256(document_id.encode()).hexdigest()[:32] + ".json")
    saved.write_bytes(content)
    if os.name != "nt":
        saved.chmod(0o600)
    before = saved.stat()
    for empty_id in ("empty-one", "empty-two"):
        empty = private.root / (hashlib.sha256(empty_id.encode()).hexdigest()[:32] + ".json")
        empty.write_bytes(b"[]")
        if os.name != "nt":
            empty.chmod(0o600)
        assert private.read(empty_id) == []
    assert private.read(document_id) == expected
    login(client, "jribeiro@sensorpoint.pt")
    response = client.get(f"/api/file/{draft.stem}")
    assert response.status_code == 200
    assert expected[0]["location"] in json.dumps(response.get_json(), ensure_ascii=False)
    assert "legacy-site-20260928" in json.dumps(response.get_json())
    login(client, "outro@sensorpoint.pt")
    assert "legacy-site-20260928" not in json.dumps(client.get(f"/api/file/{draft.stem}").get_json())
    assert saved.read_bytes() == content
    assert saved.stat().st_mtime_ns == before.st_mtime_ns
    assert saved.stat().st_mode == before.st_mode
    assert len(list(private.root.glob("*.json"))) == 3
    assert not (private.root / "pdf").exists()
    if os.name != "nt":
        assert private.root.stat().st_mode & 0o777 == 0o700
        assert all(path.stat().st_mode & 0o777 == 0o600 for path in private.root.glob("*.json"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions verified by Linux CI")
def test_private_writes_keep_700_directories_and_600_files(tmp_path):
    private = MaintenancePrivateService(tmp_path / "sadi")
    private.write("synthetic", [{"location": "Local fictício"}])
    private.write("synthetic", [{"location": "Local fictício revisto"}])
    assert private.root.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in private.root.iterdir())
