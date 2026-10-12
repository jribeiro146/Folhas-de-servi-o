"""Folder races use synthetic transports; no Graph or operational data."""
import copy
import json

import pytest

from src.services.graph_storage_service import GraphConfig, GraphConflictError, GraphStorageError, GraphStorageService
from src.services.graph_sync_queue import GraphSyncQueue
from src.services.local_changes import mark_dirty
from src.services.photo_attachment_service import PHOTO_FOLDER_NAME, PhotoAttachmentService
from tests.test_audit_removals import photo_upload
from tests.test_graph_publication_recovery import RecoverableCreation, draft


class FolderRace(GraphStorageService):
    def __init__(self, outcome="conflict", item=None):
        super().__init__(GraphConfig("test", "test", "test", "test"))
        self.item = item or {"id": "shared-folder", "name": "Activas", "folder": {"childCount": 1}}
        self.outcome = outcome
        self.visible = False
        self.calls = []
        self.contents = {"existing-child": b"preserve these synthetic bytes"}

    def _graph_json(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if method == "GET":
            if not self.visible:
                raise GraphStorageError("Microsoft Graph HTTP 404: synthetic missing")
            return copy.deepcopy(self.item)
        assert method == "POST"
        body = json.loads(kwargs["data"])
        assert body["@microsoft.graph.conflictBehavior"] == "fail"
        self.visible = True
        if self.outcome == "conflict":
            raise GraphConflictError("Concurrent creator", status_code=409)
        if self.outcome == "lost":
            raise GraphStorageError("Lost creation reply")
        if self.outcome == "replaced":
            return {**self.item, "id": "original-created-id"}
        if self.outcome == "412":
            raise GraphConflictError("Precondition", status_code=412)
        return copy.deepcopy(self.item)


@pytest.mark.parametrize("outcome", ["conflict", "success"])
def test_shared_root_race_preserves_existing_children_and_confirms_identity(outcome):
    graph = FolderRace(outcome)
    before = copy.deepcopy(graph.contents)
    assert graph.ensure_folder_path("/Activas/")["id"] == "shared-folder"
    assert [m for m, _, _ in graph.calls] == ["GET", "POST", "GET"]
    assert graph.contents == before
    assert graph.ensure_folder_path("Activas")["id"] == "shared-folder"
    assert [m for m, _, _ in graph.calls].count("POST") == 1


@pytest.mark.parametrize("visible", [False, True])
@pytest.mark.parametrize("item", [
    {"id": "file", "name": "Activas", "file": {}},
    {"id": "ambiguous", "name": "Activas", "folder": {}, "file": {}},
    {"id": "remote", "name": "Activas", "folder": {}, "remoteItem": {}},
    {"id": "deleted", "name": "Activas", "folder": {}, "deleted": {}},
    {"id": "", "name": "Activas", "folder": {}},
    {"id": 42, "name": "Activas", "folder": {}},
    {"id": "wrong-name", "name": "Elsewhere", "folder": {}},
    {"id": "wrong-type", "name": "Activas", "folder": None},
    {"id": "wrong-drive", "name": "Activas", "folder": {}, "parentReference": {"driveId": "another-drive"}},
])
def test_shared_root_invalid_existing_or_conflicting_item_stops_before_descendants(item, visible):
    graph = FolderRace(item=item)
    graph.visible = visible
    before = copy.deepcopy(graph.contents)
    with pytest.raises(GraphConflictError):
        graph.ensure_folder_path("Activas/child")
    assert graph.contents == before
    assert all("child" not in path.rsplit(":/", 1)[-1] for method, path, _ in graph.calls if method == "GET")
    assert sum(m == "POST" for m, _, _ in graph.calls) == (0 if visible else 1)


def test_successful_post_cannot_adopt_a_replacement_identity():
    graph = FolderRace("replaced")
    with pytest.raises(GraphConflictError):
        graph.ensure_folder_path("Activas/child")
    assert [m for m, _, _ in graph.calls] == ["GET", "POST", "GET"]


def test_shared_root_lost_reply_is_safely_recovered_without_second_post():
    graph = FolderRace("lost")
    with pytest.raises(GraphStorageError, match="Lost"):
        graph.ensure_folder_path("Activas")
    assert graph.ensure_folder_path("Activas")["id"] == "shared-folder"
    assert [m for m, _, _ in graph.calls].count("POST") == 1


def test_only_409_is_reconciled_as_shared_folder_conflict():
    graph = FolderRace("412")
    with pytest.raises(GraphConflictError) as failure:
        graph.ensure_folder_path("Activas")
    assert failure.value.status_code == 412
    assert [m for m, _, _ in graph.calls] == ["GET", "POST"]


def test_nested_shared_folder_is_created_by_stable_parent_id(monkeypatch):
    graph = GraphStorageService(GraphConfig("test", "test", "test", "test"))
    root = {"id": "root-id", "name": "Arquivo", "folder": {}}
    child = {"id": "cancel-id", "name": "Canceladas", "folder": {},
             "parentReference": {"id": "root-id", "driveId": "test"}}
    calls = []
    created = []
    def transport(method, path, **kwargs):
        calls.append((method, path))
        if path == "/drives/test/root:/Arquivo:":
            return root
        if method == "POST":
            assert path == "/drives/test/items/root-id/children"
            assert json.loads(kwargs["data"])["@microsoft.graph.conflictBehavior"] == "fail"
            created.append(child)
            raise GraphConflictError("Race", status_code=409)
        assert path == "/drives/test/items/root-id:/Canceladas:"
        if not created:
            raise GraphStorageError("Microsoft Graph HTTP 404: missing")
        return child
    monkeypatch.setattr(graph, "_graph_json", transport)
    assert graph.ensure_folder_path("Arquivo/Canceladas") == child
    assert len(calls) == 4


def test_child_with_contradictory_parent_is_rejected():
    graph = FolderRace(item={"id": "child", "name": "photos", "folder": {}, "parentReference": {"id": "foreign"}})
    with pytest.raises(GraphConflictError):
        graph._ensure_child_folder("owned-parent", "photos")


@pytest.mark.parametrize("archive", [False, True])
@pytest.mark.parametrize("kind", ["folder", "file"])
def test_owned_staging_conflict_never_adopts_foreign_item_even_on_later_save(tmp_path, monkeypatch, archive, kind):
    source = draft(tmp_path)
    graph = RecoverableCreation(None)
    ownership = source.parent / (".fs-archive-owner.json" if archive else ".fs-upload-owner.json")
    calls = []
    foreign = {}
    def collision(method, path, **kwargs):
        assert method == "POST" and path.endswith("/items/shared-root/children")
        body = json.loads(kwargs["data"])
        assert body["@microsoft.graph.conflictBehavior"] == "fail"
        foreign.update({"id": "foreign-id", "name": body["name"], kind: {}, "eTag": "v1"})
        graph.items["Activas/" + body["name"]] = foreign
        calls.append(method)
        raise GraphConflictError("Explicit conflict", status_code=409)
    monkeypatch.setattr(graph, "_graph_json", collision)
    for _ in range(2):
        with pytest.raises(GraphConflictError):
            graph._create_owned_folder(source.parent, ownership, "Activas")
    assert calls == ["POST"]
    assert graph.writes == []
    proof = json.loads(ownership.read_text())
    assert proof["creation_conflict"] is True and "id" not in proof
    assert graph.items["Activas/" + foreign["name"]] == foreign


@pytest.mark.parametrize("legacy_receipt", [False, True])
def test_preexisting_random_staging_name_is_not_creation_proof(tmp_path, monkeypatch, legacy_receipt):
    source = draft(tmp_path)
    graph = RecoverableCreation(None)
    name = ".fs-upload-" + "a" * 32
    owner = source.parent / ".fs-upload-owner.json"
    if legacy_receipt:
        owner.write_text(json.dumps({"temporary_name": name}))
    monkeypatch.setattr("src.services.graph_storage_service.secrets.token_hex", lambda _: "a" * 32)
    graph.items["Activas/" + name] = {"id": "foreign", "name": name, "folder": {}, "eTag": "v1"}
    with pytest.raises(GraphConflictError):
        graph.upload_active_bundle(source)
    assert graph.created == 0 and graph.writes == []
    assert "id" not in json.loads(owner.read_text())


@pytest.mark.parametrize("archive", [False, True])
@pytest.mark.parametrize("kind", ["folder", "file"])
def test_photo_creation_race_stays_under_owned_parent_and_preserves_foreign_bundle(tmp_path, monkeypatch, archive, kind):
    source = draft(tmp_path)
    PhotoAttachmentService(source).apply([photo_upload()], [])
    graph = FolderRace(item={"id": "photos-id", "name": PHOTO_FOLDER_NAME, kind: {}})
    foreign = {"same-human-bundle-name": b"other operation data"}
    writes = []
    def upload(local, remote, **kwargs):
        assert kwargs["parent_item_id"] == "photos-id"
        writes.append(local.name)
        return {"id": "new-photo", "eTag": "v1"}
    monkeypatch.setattr(graph, "upload_file", upload)
    kwargs = {"archive_folder_id" if archive else "active_folder_id": "owned-parent"}
    if kind == "file":
        with pytest.raises(GraphConflictError):
            graph._upload_photo_folder(source.parent, "Activas/same-human-bundle-name", **kwargs)
        assert writes == []
    else:
        assert len(graph._upload_photo_folder(source.parent, "Activas/same-human-bundle-name", **kwargs)) == 1
        assert len(writes) == 1
    assert all("/items/owned-parent" in path for _, path, _ in graph.calls)
    assert foreign == {"same-human-bundle-name": b"other operation data"}


def test_missing_owned_photo_parent_never_recreates_bundle_ancestors(tmp_path, monkeypatch):
    source = draft(tmp_path)
    PhotoAttachmentService(source).apply([photo_upload()], [])
    graph = FolderRace()
    calls = []
    def missing_parent(method, path, **kwargs):
        calls.append((method, path))
        assert "/items/removed-parent" in path
        raise GraphStorageError("Microsoft Graph HTTP 404: missing parent")
    monkeypatch.setattr(graph, "_graph_json", missing_parent)
    with pytest.raises(GraphStorageError):
        graph._upload_photo_folder(source.parent, "Activas/replacement-bundle", active_folder_id="removed-parent")
    assert [m for m, _ in calls] == ["GET", "POST"]


def test_owned_conflict_receipt_survives_queue_staging_and_new_save(tmp_path, monkeypatch):
    source = draft(tmp_path)
    graph = RecoverableCreation(None)
    posts = []
    def conflict(method, path, **kwargs):
        assert method == "POST"
        body = json.loads(kwargs["data"])
        posts.append(body["name"])
        graph.items["Activas/" + body["name"]] = {
            "id": "foreign", "name": body["name"], "folder": {}, "eTag": "v1"}
        raise GraphConflictError("Explicit race", status_code=409)
    monkeypatch.setattr(graph, "_graph_json", conflict)
    queue = GraphSyncQueue(graph, tmp_path / "queue.sqlite3", auto_start=False)
    first = queue.enqueue("upload_active", {"draft_path": str(source), "fail_if_exists": True})
    mark_dirty(source.parent)
    second = queue.enqueue("upload_active", {"draft_path": str(source)})
    queue.run_until_idle()
    assert all(queue.status(job["id"])["status"] == "failed" for job in (first, second))
    assert not queue.status(second["id"])["will_retry"]
    assert len(posts) == 1 and graph.writes == []
    proof = json.loads((source.parent / ".fs-upload-owner.json").read_text())
    assert proof["creation_conflict"] and "id" not in proof


def test_known_bundle_id_does_not_allow_file_in_place_of_folder(tmp_path):
    source = draft(tmp_path)
    graph = RecoverableCreation(None)
    graph.items["Activas/" + source.parent.name] = {
        "id": "known-id", "name": source.parent.name, "file": {}, "eTag": "v1"}
    (source.parent / ".graph_bundle.json").write_text('{"id":"known-id"}')
    with pytest.raises(GraphConflictError):
        graph.upload_active_bundle(source)
    assert graph.writes == [] and graph.created == 0
