"""Explicit signature removals survive publication, reload and retries."""
import base64
from pathlib import Path
import pytest

from src.services.bundle_removals import read_removals, write_removals, copy_confirmations
from src.services.graph_storage_service import GraphStorageService, GraphConfig, GraphConflictError, GraphStorageError
from src.services.graph_sync_queue import GraphSyncQueue
from src.services.signature_service import SignatureService, CLIENT_SIGNATURE_LABEL
from src.services.local_changes import mark_dirty, dirty_version

PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9XkAAAAASUVORK5CYII="


class BundleGraph(GraphStorageService):
    def __init__(self):
        super().__init__(GraphConfig("synthetic", "synthetic", "synthetic", "synthetic", active_path="Activas"))
        self.files = {}
        self.serial = 0
        self.deletes = []
        self.creations = []

    def _get_item_by_path(self, path):
        if path == "Activas/draft":
            return {"id": "bundle", "name": "draft", "eTag": "folder-v1", "folder": {"childCount": len(self.files)}}
        name = path.removeprefix("Activas/draft/")
        return self.files.get(name, {}).get("item")

    def _list_children(self, item_id):
        assert item_id == "bundle"
        return [dict(f["item"]) for f in self.files.values()]

    def upload_file(self, file, remote_path, *, expected_etag=None, create_only=False):
        name = remote_path.removeprefix("Activas/draft/")
        existing = self.files.get(name)
        if create_only:
            self.creations.append(name)
            if existing:
                raise GraphConflictError("File exists", status_code=412)
        elif expected_etag and (not existing or existing["item"]["eTag"] != expected_etag):
            raise GraphConflictError("Remote edit", status_code=412)
        self.serial += 1
        data = file.read_bytes()
        item = {"id": "file-" + name, "name": Path(name).name, "eTag": f"v{self.serial}",
                "size": len(data), "file": {"mimeType": "synthetic"}}
        self.files[name] = {"data": data, "item": item}
        return item

    def _delete_item_by_id(self, item_id, *, expected_etag=None):
        name, entry = next((n, f) for n, f in self.files.items() if f["item"]["id"] == item_id)
        assert expected_etag == entry["item"]["eTag"]
        self.deletes.append(name)
        del self.files[name]

    def download_item(self, item_id, destination):
        entry = next(f for f in self.files.values() if f["item"]["id"] == item_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(entry["data"])


def signed_draft(tmp_path):
    directory = tmp_path / "live" / "draft"
    directory.mkdir(parents=True)
    file = directory / "draft.xlsx"
    file.write_bytes(b"synthetic workbook")
    (directory / ".graph_bundle.json").write_text('{"id":"bundle"}')
    SignatureService(file).save_from_form_data({CLIENT_SIGNATURE_LABEL: PNG})
    graph = BundleGraph()
    graph.upload_active_bundle(file)
    return graph, file


def clear_signature(file):
    SignatureService(file).save_from_form_data({CLIENT_SIGNATURE_LABEL: None})


def test_deleted_signature_does_not_return_in_new_or_existing_cache(tmp_path):
    graph, file = signed_draft(tmp_path)
    stale = graph._sync_active_bundle(graph._get_item_by_path("Activas/draft"), tmp_path / "cache")
    assert SignatureService(stale).read_as_data_urls()[CLIENT_SIGNATURE_LABEL] == PNG
    clear_signature(file)
    graph.upload_active_bundle(file)
    assert graph.deletes == ["draft__assinatura_cliente.png"]
    assert ".fs-removals.json" not in graph.files
    for cache in [tmp_path / "cache", tmp_path / "new-cache"]:
        reloaded = graph._sync_active_bundle(graph._get_item_by_path("Activas/draft"), cache)
        assert SignatureService(reloaded).read_as_data_urls()[CLIENT_SIGNATURE_LABEL] is None


def test_changed_remote_signature_is_preserved_and_publication_is_blocked(tmp_path):
    graph, file = signed_draft(tmp_path)
    clear_signature(file)
    graph.files["draft__assinatura_cliente.png"]["item"]["eTag"] = "external-version"
    queue = GraphSyncQueue(graph, tmp_path / "queue.sqlite3", auto_start=False)
    mark_dirty(file.parent)
    job = queue.enqueue("upload_active", {"draft_path": str(file)})
    queue.run_until_idle()
    assert queue.status(job["id"])["status"] == "failed"
    assert not queue.status(job["id"])["will_retry"]
    assert graph.deletes == [] and dirty_version(file.parent)


def test_remove_then_recreate_uses_deleted_metadata_without_old_etag(tmp_path):
    graph, file = signed_draft(tmp_path)
    queue = GraphSyncQueue(graph, tmp_path / "queue.sqlite3", auto_start=False)
    clear_signature(file)
    mark_dirty(file.parent)
    removed = queue.enqueue("upload_active", {"draft_path": str(file)})
    # Recreate before the first snapshot has been uploaded.
    SignatureService(file).save_from_form_data({CLIENT_SIGNATURE_LABEL: PNG})
    mark_dirty(file.parent)
    recreated = queue.enqueue("upload_active", {"draft_path": str(file)})
    queue.run_until_idle()
    assert queue.status(removed["id"])["status"] == "complete"
    assert queue.status(recreated["id"])["status"] == "complete"
    assert graph.creations == ["draft__assinatura_cliente.png"]
    assert SignatureService(file).read_as_data_urls()[CLIENT_SIGNATURE_LABEL] == PNG
    assert not dirty_version(file.parent)


def test_lost_delete_response_is_reconciled_without_a_second_delete(tmp_path):
    graph, file = signed_draft(tmp_path)
    clear_signature(file)
    delete = graph._delete_item_by_id
    def lost(*args, **kwargs):
        delete(*args, **kwargs)
        raise GraphStorageError("Response lost after deletion")
    graph._delete_item_by_id = lost
    with pytest.raises(GraphStorageError):
        graph.upload_active_bundle(file)
    graph._delete_item_by_id = delete
    graph.upload_active_bundle(file)
    assert len(graph.deletes) == 1
    assert read_removals(file.parent)["draft__assinatura_cliente.png"]["confirmed"]


def test_older_confirmation_does_not_acknowledge_a_new_removal(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    write_removals(source, {"signature.png": {"token": "old", "confirmed": True}})
    write_removals(target, {"signature.png": {"token": "new", "confirmed": False}})
    copy_confirmations(source, target)
    assert not read_removals(target)["signature.png"]["confirmed"]


def test_corrupt_removal_journal_never_authorizes_deletion(tmp_path):
    graph, file = signed_draft(tmp_path)
    (file.parent / ".fs-removals.json").write_text('{"version":1,"files":{"../foreign":{"token":"bad"}}}')
    with pytest.raises(ValueError, match="inválido"):
        graph.upload_active_bundle(file)
    assert not graph.deletes


def test_conditional_creation_does_not_overwrite_a_reintroduced_remote_signature(tmp_path):
    graph, file = signed_draft(tmp_path)
    clear_signature(file)
    graph.upload_active_bundle(file)
    SignatureService(file).save_from_form_data({CLIENT_SIGNATURE_LABEL: PNG})
    graph.files["draft__assinatura_cliente.png"] = {"data": b"external",
        "item": {"id": "external", "name": "draft__assinatura_cliente.png", "eTag": "external-v1"}}
    with pytest.raises(GraphConflictError):
        graph.upload_active_bundle(file)
    assert graph.files["draft__assinatura_cliente.png"]["data"] == b"external"


def test_recreation_after_refresh_keeps_conditional_creation_receipt(tmp_path):
    graph, file = signed_draft(tmp_path)
    clear_signature(file)
    graph.upload_active_bundle(file)
    graph._sync_active_bundle(graph._get_item_by_path("Activas/draft"), file.parent.parent)
    assert not (file.parent / "draft__assinatura_cliente.png.graph.json").exists()
    SignatureService(file).save_from_form_data({CLIENT_SIGNATURE_LABEL: PNG})
    graph.upload_active_bundle(file)
    assert graph.creations == ["draft__assinatura_cliente.png"]
