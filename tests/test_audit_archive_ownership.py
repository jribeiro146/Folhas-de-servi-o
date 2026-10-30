"""Archive creation must never adopt or overwrite an unrelated remote bundle."""
import json
from dataclasses import replace

import pytest

from src.services.graph_storage_service import GraphStorageService, GraphConfig, GraphConflictError, GraphStorageError
from tests.test_graph_publication_recovery import RecoverableCreation


def archive(tmp_path):
    folder = tmp_path / "synthetic"
    folder.mkdir()
    file = folder / "synthetic.xlsx"
    file.write_bytes(b"synthetic workbook")
    return file


@pytest.mark.parametrize("lost_method", [None, "POST", "PATCH"])
def test_owned_archive_creation_recovers_lost_responses(tmp_path, lost_method):
    file = archive(tmp_path)
    graph = RecoverableCreation(lost_method)
    graph.config = replace(graph.config, archive_path="Activas")
    if lost_method:
        with pytest.raises(GraphStorageError):
            graph._owned_archive_folder(file.parent)
    item = graph._owned_archive_folder(file.parent)
    assert item["id"] == "created-folder"
    assert graph.created == 1
    assert graph._owned_archive_folder(file.parent)["id"] == item["id"]
    assert GraphStorageService._is_graph_metadata_file(file.parent / ".fs-archive-owner.json")


@pytest.mark.parametrize("proof", [None, "active", "different-id", "invalid"])
def test_unrelated_archive_is_preserved_before_any_write(tmp_path, proof):
    file = archive(tmp_path)
    graph = RecoverableCreation(None)
    graph.config = replace(graph.config, archive_path="Activas")
    graph.items["Activas/" + file.parent.name] = {"id": "foreign-folder", "folder": {}}
    if proof == "active":
        (file.parent / ".graph_bundle.json").write_text('{"id":"foreign-folder"}')
    elif proof == "different-id":
        (file.parent / ".fs-archive-owner.json").write_text('{"id":"original-folder"}')
    elif proof == "invalid":
        (file.parent / ".fs-archive-owner.json").write_text('[]')
    with pytest.raises(GraphConflictError):
        graph.upload_archive_bundle(file)
    assert graph.created == 0
    assert graph.items["Activas/" + file.parent.name]["id"] == "foreign-folder"


@pytest.mark.parametrize("same_content", [False, True])
def test_lost_file_creation_is_read_only_reconciled_by_content(tmp_path, monkeypatch, same_content):
    file = archive(tmp_path)
    graph = GraphStorageService(GraphConfig("test", "test", "test", "test"))
    requests = []
    def transport(method, path, **kwargs):
        requests.append((method, path, kwargs))
        if method == "PUT":
            assert "items/owned-parent:/synthetic.xlsx:/content?@microsoft.graph.conflictBehavior=fail" in path
            assert kwargs["headers"] == {"If-None-Match": "*"}
            raise GraphConflictError("Existing file", status_code=409)
        assert method == "GET"
        return file.read_bytes() if same_content else b"different remote bytes"
    monkeypatch.setattr(graph, "_graph_bytes", transport)
    monkeypatch.setattr(graph, "_graph_json", lambda *a, **kw: {
        "id": "created-file", "eTag": "v1", "name": file.name, "file": {}})
    if same_content:
        assert graph._upload_archive_file(file, "Archive/ignored-path", "owned-parent")["id"] == "created-file"
    else:
        with pytest.raises(GraphConflictError, match="diferente"):
            graph._upload_archive_file(file, "Archive/ignored-path", "owned-parent")
    assert [r[0] for r in requests] == ["PUT", "GET"]


def test_archive_update_keeps_version_and_stable_parent(tmp_path, monkeypatch):
    file = archive(tmp_path)
    graph = GraphStorageService(GraphConfig("test", "test", "test", "test"))
    graph._write_item_meta(graph._item_meta_path(file), {"id": "owned-file", "eTag": "v1"})
    def transport(method, path, **kwargs):
        assert method == "PUT"
        assert "items/owned-parent:/synthetic.xlsx:/content" in path
        assert kwargs["headers"] == {"If-Match": "v1"}
        raise GraphConflictError("External edit", status_code=412)
    monkeypatch.setattr(graph, "_graph_bytes", transport)
    with pytest.raises(GraphConflictError):
        graph._upload_archive_file(file, "Archive/ignored-path", "owned-parent")


def test_archive_photos_use_owned_parent_and_exclude_local_proof(tmp_path, monkeypatch):
    from tests.test_audit_removals import photo_upload
    from src.services.photo_attachment_service import PhotoAttachmentService, PHOTO_FOLDER_NAME
    file = archive(tmp_path)
    PhotoAttachmentService(file).apply([photo_upload()], [])
    (file.parent / ".fs-archive-owner.json").write_text('{"id":"owned-parent"}')
    graph = GraphStorageService(GraphConfig("test", "test", "test", "test"))
    monkeypatch.setattr(graph, "_owned_archive_folder", lambda _: {"id": "owned-parent"})
    monkeypatch.setattr(graph, "_list_children", lambda _: [])
    created = []
    def create(method, path, **kwargs):
        if method == "GET":
            assert path.endswith("/items/owned-parent:/" + PHOTO_FOLDER_NAME + ":")
            if not created:
                raise GraphStorageError("Microsoft Graph HTTP 404: missing")
            return created[0]
        assert method == "POST" and path.endswith("/items/owned-parent/children")
        assert json.loads(kwargs["data"])["@microsoft.graph.conflictBehavior"] == "fail"
        created.append({"id": "owned-photos", "name": PHOTO_FOLDER_NAME, "folder": {}})
        return created[0]
    monkeypatch.setattr(graph, "_graph_json", create)
    writes = []
    def upload(local, remote, parent):
        writes.append((local.name, parent))
        return {"id": "file", "eTag": "v1", "name": local.name}
    monkeypatch.setattr(graph, "_upload_archive_file", upload)
    graph.upload_archive_bundle(file)
    assert len(writes) == 2
    assert writes[0] == (file.name, "owned-parent")
    assert writes[1][1] == "owned-photos"
    assert all(name != ".fs-archive-owner.json" for name, _ in writes)


def test_unknown_archive_blocks_pdf_conversion(tmp_path):
    file = archive(tmp_path)
    file.with_name(file.stem + "__folha_final.html").write_text("<html>synthetic</html>")
    graph = RecoverableCreation(None)
    graph.config = replace(graph.config, archive_path="Activas")
    graph.items["Activas/" + file.parent.name] = {"id": "foreign-folder", "folder": {}}
    with pytest.raises(GraphConflictError):
        graph.export_archive_pdf(file)
    assert graph.created == 0
