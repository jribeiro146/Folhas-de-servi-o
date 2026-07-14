import json
import shutil
from pathlib import Path

import pytest

from src.services.archive_service import ArchiveService
from src.services.editing_state_service import (
    EditingStateService,
    EditorIdentity,
    LeaseConflictError,
    RevisionConflictError,
)
from src.services.file_service import FileService
from src.services.graph_storage_service import GraphConfig, GraphStorageService
from src.web.application import create_app


FIXTURE = Path(__file__).parent / "fixtures" / "test_sample.xlsx"


@pytest.fixture(autouse=True)
def disable_web_auth(monkeypatch):
    """Keep web tests independent from the developer's local .env file."""
    import src.web.application as application_module

    monkeypatch.setattr(application_module, "ACTIVE_AUTH_PROVIDER", "none")
    monkeypatch.setattr(application_module, "AUTH_ENABLED", False)
    monkeypatch.setattr(application_module, "STORAGE_BACKEND", "local")


def make_active_file(root: Path, name: str) -> Path:
    active = root / "Excel" / "Activas"
    active.mkdir(parents=True, exist_ok=True)
    destination = active / f"{name}.xlsx"
    shutil.copy2(FIXTURE, destination)
    return destination


def test_lease_blocks_other_user_and_other_tab(tmp_path):
    path = make_active_file(tmp_path, "2026_5001")
    service = EditingStateService(tmp_path / "editing", lease_seconds=120)
    first_user = EditorIdentity("user-a", "Utilizador A")

    first = service.acquire_lease(path, first_user, "tab-a")

    with pytest.raises(LeaseConflictError):
        service.acquire_lease(path, EditorIdentity("user-b", "Utilizador B"), "tab-b")
    with pytest.raises(LeaseConflictError):
        service.acquire_lease(path, first_user, "second-tab")

    assert first["lease"]["owner_name"] == "Utilizador A"
    assert first["lease"]["token"]


def test_explicit_logout_release_clears_every_user_lease(tmp_path):
    first_path = make_active_file(tmp_path, "2026_5004")
    second_path = make_active_file(tmp_path, "2026_5005")
    service = EditingStateService(tmp_path / "editing")
    first_user = EditorIdentity("user-a", "Utilizador A")

    service.acquire_lease(first_path, first_user, "tab-a")
    service.acquire_lease(second_path, first_user, "tab-b")

    assert service.release_user_leases(first_user.id) == 2
    replacement = service.acquire_lease(
        first_path,
        EditorIdentity("user-b", "Utilizador B"),
        "tab-c",
    )
    assert replacement["lease"]["owner_id"] == "user-b"


def test_abandoned_lease_expires_and_can_be_reacquired(tmp_path, monkeypatch):
    import src.services.editing_state_service as editing_module

    path = make_active_file(tmp_path, "2026_5002")
    service = EditingStateService(tmp_path / "editing", lease_seconds=10)
    now = {"value": 1000.0}
    monkeypatch.setattr(editing_module.time, "time", lambda: now["value"])

    service.acquire_lease(path, EditorIdentity("user-a", "Utilizador A"), "tab-a")
    now["value"] = 1011.0
    second = service.acquire_lease(path, EditorIdentity("user-b", "Utilizador B"), "tab-b")

    assert second["lease"]["owner_id"] == "user-b"


def test_autosave_is_idempotent_and_rejects_stale_revision(tmp_path):
    path = make_active_file(tmp_path, "2026_5003")
    service = EditingStateService(tmp_path / "editing")
    identity = EditorIdentity("user-a", "Utilizador A")
    lease = service.acquire_lease(path, identity, "tab-a")
    arguments = {
        "document_id": lease["document_id"],
        "identity": identity,
        "client_id": "tab-a",
        "lease_token": lease["lease"]["token"],
        "base_revision": lease["revision"],
        "idempotency_key": "autosave-1",
        "document": {"intervention_report": "Versão A"},
    }

    saved = service.save_autosave(**arguments)
    replay = service.save_autosave(**arguments)

    assert replay["revision"] == saved["revision"]
    assert replay["server_document"]["intervention_report"] == "Versão A"
    with pytest.raises(RevisionConflictError) as conflict:
        service.save_autosave(
            **{
                **arguments,
                "idempotency_key": "autosave-2",
                "document": {"intervention_report": "Versão B"},
            }
        )
    assert conflict.value.snapshot["server_document"]["intervention_report"] == "Versão A"


def test_web_leases_isolate_files_and_autosave_conflicts(tmp_path):
    first_path = make_active_file(tmp_path, "2026_5101")
    make_active_file(tmp_path, "2026_5102")
    app = create_app(
        file_service=FileService(first_path.parent),
        archive_service=ArchiveService(),
        editing_state_service=EditingStateService(tmp_path / "editing"),
    )
    first_client = app.test_client()
    second_client = app.test_client()

    first_lease_response = first_client.post(
        "/api/file/2026_5101/lease",
        json={"client_id": "first-tab"},
    )
    first_lease = first_lease_response.get_json()["editing"]
    locked_response = second_client.post(
        "/api/file/2026_5101/lease",
        json={"client_id": "second-tab"},
    )
    other_file_response = second_client.post(
        "/api/file/2026_5102/lease",
        json={"client_id": "second-tab"},
    )

    assert first_lease_response.status_code == 200
    assert locked_response.status_code == 423
    assert locked_response.get_json()["code"] == "lease_conflict"
    assert other_file_response.status_code == 200

    edit = {
        "document_id": first_lease["document_id"],
        "client_id": "first-tab",
        "lease_token": first_lease["lease"]["token"],
        "base_revision": first_lease["revision"],
        "idempotency_key": "web-autosave-1",
    }
    autosave_response = first_client.post(
        "/api/file/2026_5101/autosave",
        json={
            "document": {"intervention_report": "Autosave web"},
            "_edit": edit,
        },
    )
    replay_response = first_client.post(
        "/api/file/2026_5101/autosave",
        json={
            "document": {"intervention_report": "Autosave web"},
            "_edit": edit,
        },
    )
    stale_response = first_client.post(
        "/api/file/2026_5101/autosave",
        json={
            "document": {"intervention_report": "Revisão obsoleta"},
            "_edit": {**edit, "idempotency_key": "web-autosave-2"},
        },
    )

    assert autosave_response.status_code == 200
    assert replay_response.status_code == 200
    assert replay_response.get_json()["editing"]["revision"] == autosave_response.get_json()["editing"]["revision"]
    assert stale_response.status_code == 409
    assert stale_response.get_json()["code"] == "revision_conflict"
    assert stale_response.get_json()["editing"]["server_document"]["intervention_report"] == "Autosave web"


def test_graph_upload_uses_if_match_and_persists_new_etag(tmp_path, monkeypatch):
    local_file = tmp_path / "draft.json"
    local_file.write_text("{}", encoding="utf-8")
    service = GraphStorageService(
        GraphConfig("tenant", "client", "secret", "drive")
    )
    calls = []

    def fake_graph_bytes(method, path, data=None, content_type=None, headers=None):
        calls.append({"method": method, "path": path, "headers": headers})
        return json.dumps({
            "id": "item-1",
            "name": "draft.json",
            "size": 2,
            "lastModifiedDateTime": "2026-07-10T10:00:00Z",
            "eTag": '"etag-new"',
        }).encode("utf-8")

    monkeypatch.setattr(service, "_graph_bytes", fake_graph_bytes)
    item = service.upload_file(local_file, "Activas/draft.json", expected_etag='"etag-old"')
    service._write_item_meta(service._item_meta_path(local_file), item)
    metadata = json.loads(service._item_meta_path(local_file).read_text(encoding="utf-8"))

    assert calls[0]["headers"] == {"If-Match": '"etag-old"'}
    assert metadata["eTag"] == '"etag-new"'


def test_cache_validation_includes_graph_etag(tmp_path):
    local_file = tmp_path / "cached.xlsx"
    local_file.write_bytes(b"cache")
    metadata_path = local_file.with_name(f"{local_file.name}.graph.json")
    metadata_path.write_text(json.dumps({
        "id": "item-1",
        "size": 5,
        "lastModifiedDateTime": "2026-07-10T10:00:00Z",
        "eTag": '"etag-old"',
    }), encoding="utf-8")
    item = {
        "id": "item-1",
        "size": 5,
        "lastModifiedDateTime": "2026-07-10T10:00:00Z",
        "eTag": '"etag-new"',
    }

    assert not GraphStorageService._is_cache_current(local_file, metadata_path, item)
    item["eTag"] = '"etag-old"'
    assert GraphStorageService._is_cache_current(local_file, metadata_path, item)


def test_feature_five_assets_and_api_are_not_http_cached(tmp_path):
    path = make_active_file(tmp_path, "2026_5201")
    app = create_app(
        file_service=FileService(path.parent),
        archive_service=ArchiveService(),
        editing_state_service=EditingStateService(tmp_path / "editing"),
    )
    client = app.test_client()

    html = client.get("/?file=2026_5201").get_data(as_text=True)
    api_response = client.get("/api/file/2026_5201")
    service_worker = Path("src/web/static/service-worker.js").read_text(encoding="utf-8")

    assert 'id="recovery-panel"' in html
    assert 'id="autosave-status"' in html
    assert "editing-coordinator.js" in html
    assert "no-store" in api_response.headers["Cache-Control"]
    assert 'requestUrl.pathname.startsWith("/api/")' in service_worker
    assert 'caches.match("/")' not in service_worker
    assert "editing-state.css" in service_worker
    coordinator = Path("src/web/static/js/editing-coordinator.js").read_text(encoding="utf-8")
    editing_css = Path("src/web/static/css/editing-state.css").read_text(encoding="utf-8")
    assert "edição guardada neste dispositivo" in coordinator
    assert "setCommitActionsEnabled(false)" in coordinator
    assert ".editing-lock-banner[hidden]" in editing_css
