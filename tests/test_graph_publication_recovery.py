"""Regression sequences from the production review; no external transports."""

import json
import time
from pathlib import Path

import pytest

from src.services.active_file_index import ACTIVE_INDEX_NAME
from src.services.editing_state_repository import SqliteStateRepository
from src.services.file_service import FileService
from src.services.graph_storage_service import GraphConfig, GraphConflictError, GraphStorageError, GraphStorageService
from src.services.graph_sync_queue import GraphSyncQueue
from src.services.local_changes import DIRTY_MARKER, dirty_version, mark_dirty


def draft(root):
    directory = root / "2026_9900_2026-09-28_TF"
    directory.mkdir()
    source = directory / (directory.name + ".xlsx")
    source.write_bytes(b"first save")
    return source


class ConditionalGraph(GraphStorageService):
    def __init__(self):
        super().__init__(GraphConfig("test", "test", "test", "test"))
        self.etag = "v0"
        self.requests = []
        self.content = None

    def _get_item_by_path(self, path):
        return {"id": "folder", "eTag": "folder-tag"}

    def upload_file(self, local_file, remote_path, *, expected_etag=None):
        self.requests.append((expected_etag, self.etag))
        if expected_etag != self.etag:
            raise GraphConflictError("Synthetic HTTP 412")
        self.etag = "v" + str(int(self.etag[1:]) + 1)
        self.content = local_file.read_bytes()
        return {"id": "workbook", "name": local_file.name, "eTag": self.etag}

    def _upload_photo_folder(self, *args, **kwargs):
        return []


def existing_draft(root):
    source = draft(root)
    GraphStorageService._write_bundle_meta(source.parent, {"id": "folder"})
    GraphStorageService._write_item_meta(GraphStorageService._item_meta_path(source),
                                        {"id": "workbook", "eTag": "v0"})
    return source


def test_two_saves_before_worker_publish_latest_snapshot_and_unblock_finalization(tmp_path):
    source = existing_draft(tmp_path)
    graph = ConditionalGraph()
    queue = GraphSyncQueue(graph, tmp_path / "queue.sqlite3", auto_start=False)
    mark_dirty(source.parent)
    first = queue.enqueue("upload_active", {"draft_path": str(source)})
    source.write_bytes(b"second save")
    mark_dirty(source.parent)
    second = queue.enqueue("upload_active", {"draft_path": str(source)})
    queue.run_until_idle()
    assert [queue.status(j["id"])["status"] for j in (first, second)] == ["complete", "complete"]
    assert graph.requests == [("v0", "v0"), ("v1", "v1")]
    assert graph.content == b"second save"
    assert not dirty_version(source.parent)
    assert not queue.has_unfinished_upload(source)


def test_real_remote_conflict_is_not_rebased_away(tmp_path):
    source = existing_draft(tmp_path)
    graph = ConditionalGraph()
    graph.etag = "v9"  # someone else's remote edit
    queue = GraphSyncQueue(graph, tmp_path / "queue.sqlite3", auto_start=False)
    job = queue.enqueue("upload_active", {"draft_path": str(source)})
    queue.run_until_idle()
    assert queue.status(job["id"])["status"] == "failed"
    assert not queue.status(job["id"])["will_retry"]
    assert queue.has_unfinished_upload(source)
    assert dirty_version(source.parent)
    assert graph.content is None


def test_partial_upload_metadata_is_reused_without_clearing_a_newer_local_edit(tmp_path):
    source = existing_draft(tmp_path)
    graph = ConditionalGraph()
    failures = [True]

    def photos(*args, **kwargs):
        if failures:
            failures.pop()
            raise GraphStorageError("Transient photo upload failure after workbook success")
        return []

    graph._upload_photo_folder = photos
    queue = GraphSyncQueue(graph, tmp_path / "queue.sqlite3", auto_start=False)
    first = queue.enqueue("upload_active", {"draft_path": str(source)})
    source.write_bytes(b"second save")
    mark_dirty(source.parent)
    second = queue.enqueue("upload_active", {"draft_path": str(source)})
    queue.run_until_idle()
    assert queue.status(second["id"])["status"] == "complete"
    assert queue.status(first["id"])["result"]["superseded_by"] == second["id"]
    assert graph.requests == [("v0", "v0"), ("v1", "v1")]
    assert graph.content == b"second save"
    assert not dirty_version(source.parent)


def test_older_retry_cannot_overwrite_a_newer_partially_published_save(tmp_path):
    source = existing_draft(tmp_path)
    graph = ConditionalGraph()
    upload = graph.upload_file
    fail_first = [True]
    photo_conflict = [True]

    def upload_with_failure(path, remote_path, **kwargs):
        if path.read_bytes() == b"first save" and fail_first:
            fail_first.pop()
            raise GraphStorageError("Transient failure before first upload")
        return upload(path, remote_path, **kwargs)

    def photos(*args, **kwargs):
        if photo_conflict:
            raise GraphConflictError("Newer workbook published, but photo conflict")
        return []

    graph.upload_file = upload_with_failure
    graph._upload_photo_folder = photos
    queue = GraphSyncQueue(graph, tmp_path / "queue.sqlite3", auto_start=False)
    first = queue.enqueue("upload_active", {"draft_path": str(source)})
    source.write_bytes(b"second save")
    mark_dirty(source.parent)
    second = queue.enqueue("upload_active", {"draft_path": str(source)})
    queue.run_until_idle()
    assert graph.content == b"second save"
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET next_attempt_at=0 WHERE id=?", (first["id"],))
    queue.run_until_idle()
    assert graph.content == b"second save"
    assert not queue.status(first["id"])["will_retry"]
    assert queue.has_unfinished_upload(source)
    with pytest.raises(ValueError, match="gravação posterior"):
        queue.retry(first["id"])
    photo_conflict.clear()
    queue.retry(second["id"])
    queue.run_until_idle()
    assert graph.content == b"second save"
    assert not queue.has_unfinished_upload(source)


@pytest.mark.parametrize("old_status", ["failed", "pending", "held"])
@pytest.mark.parametrize("legacy", [False, True])
def test_newer_confirmed_upload_retires_old_jobs_and_cannot_republish_them(tmp_path, old_status, legacy):
    source = existing_draft(tmp_path)
    queue = GraphSyncQueue(ConditionalGraph(), tmp_path / "queue.sqlite3", auto_start=False)
    old = queue.enqueue("upload_active", {"draft_path": str(source)})
    newer = queue.enqueue("upload_active", {"draft_path": str(source)})
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET status=?, retryable=0 WHERE id=?", (old_status, old["id"]))
        db.execute("UPDATE graph_sync_jobs SET status='complete' WHERE id=?", (newer["id"],))
        if legacy:
            db.execute("UPDATE graph_sync_jobs SET payload_json=? WHERE id=?",
                       (json.dumps({"draft_path": str(source)}), old["id"]))
    assert not queue.has_unfinished_upload(source)
    assert queue.status(old["id"])["result"]["superseded_by"] == newer["id"]
    with pytest.raises(ValueError):
        queue.retry(old["id"])
    assert queue._claim_next() is None


def test_earlier_success_or_another_draft_does_not_hide_latest_failure(tmp_path):
    source = existing_draft(tmp_path)
    queue = GraphSyncQueue(None, tmp_path / "queue.sqlite3", auto_start=False)
    early = queue.enqueue("upload_active", {"draft_path": str(source)})
    late = queue.enqueue("upload_active", {"draft_path": str(source)})
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET status='complete' WHERE id=?", (early["id"],))
        db.execute("UPDATE graph_sync_jobs SET status='failed' WHERE id=?", (late["id"],))
    assert queue.has_unfinished_upload(source)


def test_corrupt_legacy_upload_path_does_not_stop_claiming_other_jobs(tmp_path):
    source = draft(tmp_path)
    queue = GraphSyncQueue(None, tmp_path / "queue.sqlite3", auto_start=False)
    old = queue.enqueue("upload_active", {"draft_path": str(source)})
    ready = queue.enqueue("remove_active", {"source_name": "synthetic.xlsx"})
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET status='failed', payload_json=? WHERE id=?",
                   (json.dumps({"draft_path": "\u0000"}), old["id"]))
    assert queue._claim_next()["id"] == ready["id"]


class RecoverableCreation(GraphStorageService):
    def __init__(self, lost_method):
        super().__init__(GraphConfig("test", "test", "test", "test"))
        self.lost_method = lost_method
        self.items = {}
        self.created = 0
        self.writes = []

    def _get_item_by_path(self, path):
        return self.items.get(path)

    def ensure_folder_path(self, path):
        pass

    def _graph_json(self, method, path, **kwargs):
        assert method == "POST"
        body = json.loads(kwargs["data"])
        assert body["@microsoft.graph.conflictBehavior"] == "fail"
        key = "Activas/" + body["name"]
        assert key not in self.items
        self.created += 1
        item = {"id": "created-folder", "name": body["name"], "eTag": "folder-v1", "folder": {}}
        self.items[key] = item
        if self.lost_method == "POST":
            self.lost_method = None
            raise GraphStorageError("Response lost after successful POST")
        return item

    def _graph_bytes(self, method, path, **kwargs):
        assert method == "PATCH"
        assert kwargs["headers"] == {"If-Match": "folder-v1"}
        body = json.loads(kwargs["data"])
        assert body["@microsoft.graph.conflictBehavior"] == "fail"
        old_key = next(key for key, item in self.items.items() if item["id"] == "created-folder")
        new_key = "Activas/" + body["name"]
        if new_key in self.items:
            raise GraphConflictError("Synthetic rename collision")
        self.items[new_key] = {**self.items.pop(old_key), "name": body["name"], "eTag": "folder-v2"}
        if self.lost_method == "PATCH":
            self.lost_method = None
            raise GraphStorageError("Response lost after successful PATCH")
        return json.dumps(self.items[new_key]).encode()

    def upload_file(self, local_file, remote_path, *, expected_etag=None):
        self.writes.append(local_file.read_bytes())
        return {"id": "workbook", "eTag": "file-v1"}

    def _upload_photo_folder(self, *args, **kwargs):
        return []


@pytest.mark.parametrize("lost_method", ["POST", "PATCH"])
def test_lost_creation_response_recovers_ownership_in_next_queued_save(tmp_path, lost_method):
    source = draft(tmp_path)
    graph = RecoverableCreation(lost_method)
    queue = GraphSyncQueue(graph, tmp_path / "queue.sqlite3", auto_start=False)
    first = queue.enqueue("upload_active", {"draft_path": str(source), "fail_if_exists": True})
    source.write_bytes(b"latest save")
    mark_dirty(source.parent)
    second = queue.enqueue("upload_active", {"draft_path": str(source)})
    queue.run_until_idle()
    assert queue.status(second["id"])["status"] == "complete"
    assert queue.status(first["id"])["result"]["superseded_by"] == second["id"]
    assert graph.created == 1
    assert graph.writes == [b"latest save"]
    assert not queue.has_unfinished_upload(source)


def test_unknown_folder_is_never_adopted_after_failed_legacy_creation(tmp_path):
    source = draft(tmp_path)
    graph = RecoverableCreation(None)
    graph.items["Activas/" + source.parent.name] = {"id": "someone-else", "eTag": "v1"}
    with pytest.raises(GraphConflictError):
        graph.upload_active_bundle(source)
    assert not graph.writes and graph.created == 0


def test_folder_created_by_another_user_during_rename_is_preserved(tmp_path):
    source = draft(tmp_path)
    graph = RecoverableCreation(None)
    rename = graph._graph_bytes

    def collision(method, path, **kwargs):
        graph.items["Activas/" + source.parent.name] = {"id": "other-user", "eTag": "v9"}
        return rename(method, path, **kwargs)

    graph._graph_bytes = collision
    with pytest.raises(GraphConflictError):
        graph.upload_active_bundle(source)
    assert graph.items["Activas/" + source.parent.name]["id"] == "other-user"
    assert graph.writes == []


class InventoryGraph(GraphStorageService):
    def __init__(self, folder_name):
        super().__init__(GraphConfig("test", "test", "test", "test"))
        self.folder_name = folder_name
        self.items = []
        self.children = []
        self.downloads = []
        self.deleted = []
        self.version = b"v1"

    def list_active_items(self):
        return self.items

    def _list_children(self, item_id):
        return self.children

    def _get_item_by_path(self, path):
        return {"id": "folder", "name": self.folder_name, "eTag": "after-cleanup"}

    def download_item(self, item_id, path):
        assert item_id != "marker"
        self.downloads.append(item_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.version)

    def _graph_bytes(self, method, path, **kwargs):
        assert method == "GET" and path.endswith("/marker/content")
        return b"legacy-token"

    def _delete_item_by_id(self, item_id, *, expected_etag=None):
        assert expected_etag == "marker-v1"
        self.deleted.append(item_id)


def item(name, identity="file", etag="v1"):
    return {"id": identity, "name": name, "eTag": etag, "file": {"mimeType": "synthetic"}}


@pytest.mark.parametrize("local_state", ["clean", "imported", "edited", "edited_legacy"])
def test_remote_dirty_marker_is_not_imported_and_legacy_copy_does_not_freeze_cache(tmp_path, local_state):
    name = "draft_2026-09-28_TF"
    graph = InventoryGraph(name)
    graph.items = [{"id": "folder", "name": name, "eTag": "before-cleanup", "folder": {"childCount": 2}}]
    graph.children = [item(name + ".xlsx"), item(DIRTY_MARKER, "marker", "marker-v1"),
                      item(".graph_bundle.json", "poison"), item("x.xlsx.graph.json", "poison2")]
    local_dir = tmp_path / name
    local_dir.mkdir()
    if local_state != "clean":
        (local_dir / DIRTY_MARKER).write_bytes(b"legacy-token")
        (local_dir / (DIRTY_MARKER + ".graph.json")).write_text("{}")
        (local_dir / (name + ".xlsx")).write_bytes(b"local-edit")
    if local_state.startswith("edited"):
        mark_dirty(local_dir)
        if local_state == "edited_legacy":
            (local_dir / (DIRTY_MARKER + ".graph.json")).write_text("{}")
    graph.sync_active_files(tmp_path)
    assert graph.deleted == ([] if local_state.startswith("edited") else ["marker"])
    graph.children = [item(name + ".xlsx", etag="v2")]
    graph.version = b"v2"
    graph.sync_active_files(tmp_path)
    if local_state.startswith("edited"):
        assert dirty_version(local_dir)
        assert (local_dir / (name + ".xlsx")).read_bytes() == b"local-edit"
        assert graph.downloads == []
    else:
        assert not dirty_version(local_dir)
        assert (local_dir / (name + ".xlsx")).read_bytes() == b"v2"
        assert graph.downloads == ["file", "file"]


def test_first_inventory_skips_invalid_names_and_still_publishes_valid_files(tmp_path, caplog):
    graph = InventoryGraph("valid-folder")
    graph.items = [item("valid.xlsx"), item("bad:name.xlsx", "bad"),
                   {"id": "folder", "name": "valid-folder", "folder": {"childCount": 2}},
                   {"id": "invalid-folder", "name": "../outside", "folder": {"childCount": 1}}]
    graph.children = [item("nested.xlsx", "nested"), item("../unsafe.xlsx", "unsafe")]
    assert FileService(tmp_path, require_remote_index=True).list_excel_files() == []
    graph.sync_active_files(tmp_path)
    inventory = json.loads((tmp_path / ACTIVE_INDEX_NAME).read_text())
    assert inventory["files"] == ["valid-folder/nested.xlsx", "valid.xlsx"]
    assert len(FileService(tmp_path, require_remote_index=True).list_excel_files()) == 2
    assert graph.downloads == ["file", "nested"]
    warnings = [r for r in caplog.records if getattr(r, "event", "") == "graph_inventory_item_skipped"]
    assert len(warnings) == 3
    assert all(r.event_fields["reason"] == "invalid_name" for r in warnings)
    assert "bad:name" not in caplog.text


def test_marker_cleanup_conflict_does_not_block_download_or_create_local_dirty_marker(tmp_path):
    graph = InventoryGraph("bundle")
    graph.items = [{"id": "folder", "name": "bundle", "folder": {"childCount": 2}}]
    graph.children = [item("bundle.xlsx"), item(DIRTY_MARKER, "marker", "marker-v1")]

    def conflict(*args, **kwargs):
        raise GraphConflictError("Marker changed after listing")

    graph._delete_item_by_id = conflict
    graph.sync_active_files(tmp_path)
    assert graph.downloads == ["file"]
    assert not dirty_version(tmp_path / "bundle")


def test_cleanup_reloads_children_before_accepting_its_new_folder_etag(tmp_path):
    name = "draft_2026-09-28_TF"
    graph = InventoryGraph(name)
    graph.items = [{"id": "folder", "name": name, "eTag": "folder-v1", "folder": {"childCount": 2}}]
    graph.children = [item(name + ".xlsx")]
    graph.sync_active_files(tmp_path)  # local workbook and child metadata v1
    graph.children.append(item(DIRTY_MARKER, "marker", "marker-v1"))
    sequence = []
    original_delete = graph._delete_item_by_id
    original_get = graph._get_item_by_path
    original_list = graph._list_children

    def delete_and_concurrent_edit(*args, **kwargs):
        original_delete(*args, **kwargs)
        graph.children = [item(name + ".xlsx", etag="v2")]
        graph.version = b"v2"
        sequence.append("delete")

    def get_folder(path):
        sequence.append("folder")
        return original_get(path)

    def list_children(item_id):
        sequence.append("children")
        return list(original_list(item_id))

    graph._delete_item_by_id = delete_and_concurrent_edit
    graph._get_item_by_path = get_folder
    graph._list_children = list_children
    graph.sync_active_files(tmp_path)
    assert sequence == ["children", "delete", "folder", "children"]
    assert (tmp_path / name / (name + ".xlsx")).read_bytes() == b"v2"
    assert graph._expected_etag(tmp_path / name / (name + ".xlsx")) == "v2"


@pytest.mark.parametrize("malformed", ["[]", '{"_commit_guard":[]}', '{"_commit_guard":{"database":"missing"}}'])
def test_expired_held_job_cannot_block_worker_or_bypass_commit_on_retry(tmp_path, malformed):
    queue = GraphSyncQueue(None, tmp_path / "queue.sqlite3", auto_start=False)
    held = queue.enqueue("archive_and_remove", {"_commit_guard": {"database": str(tmp_path / "missing")}})
    next_job = queue.enqueue("remove_active", {"source_name": "synthetic.xlsx"})
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET payload_json=?, updated_at=? WHERE id=?",
                   (malformed, time.time() - 7200, held["id"]))
    assert queue._claim_next()["id"] == next_job["id"]
    state = queue.status(held["id"])
    assert state["status"] == "failed" and not state["will_retry"]
    assert state["attempts"] == 0
    assert queue.retry(held["id"])["status"] == "held"
    queue._claim_next()
    assert queue.status(held["id"])["status"] == "held"


def test_archive_conflict_never_sends_mail_or_removes_active_file(tmp_path):
    class Graph:
        def upload_archive_bundle(self, path):
            raise GraphConflictError("HTTP 412: remote archive changed")

        def remove_active_name(self, *args, **kwargs):
            pytest.fail("Active source must be preserved")

    class Mail:
        def send_prepared(self, *args, **kwargs):
            pytest.fail("No mail before confirmed archive")

    queue = GraphSyncQueue(Graph(), tmp_path / "queue.sqlite3", mail_service=Mail(), auto_start=False)
    job = queue.enqueue("archive_and_remove", {"archived_path": str(tmp_path / "draft.xlsx"),
                        "mail": {"to": "test@example.invalid"}})
    queue.run_until_idle()
    state = queue.status(job["id"])
    assert state["status"] == "failed" and not state["will_retry"]
    assert "412" in state["last_error"]


def test_held_expiration_preserves_mail_receipt_and_recovery_never_resends(tmp_path):
    editing = SqliteStateRepository(tmp_path / "editing.sqlite3")
    source = draft(tmp_path)
    calls = []

    class Graph:
        def upload_archive_bundle(self, path):
            return [path.name]

        def export_archive_pdf(self, path):
            pdf = path.with_suffix(".pdf")
            pdf.write_bytes(b"%PDF-1.4 synthetic")
            return pdf, pdf.name

        def remove_active_name(self, *args, **kwargs):
            return True

    class Mail:
        def send_prepared(self, *args, **kwargs):
            calls.append("mail")
            return {"accepted": True}

    queue = GraphSyncQueue(Graph(), tmp_path / "queue.sqlite3", mail_service=Mail(), auto_start=False)
    queue.mail_job_max_attempts = 1
    failed_once = []

    def after_mail(*args, **kwargs):
        if not failed_once:
            failed_once.append(True)
            raise RuntimeError("Failure after accepted mail, before completion")
        return None

    queue._enqueue_teams_notification = after_mail
    with editing.transaction("document", lambda: {}) as state:
        state["operations"] = {"finalize": {"status": "complete"}}
    job = queue.enqueue("archive_and_remove", {
        "archived_path": str(source), "source_name": source.parent.name,
        "mail": {"to": "recipient@example.invalid"},
        "_commit_guard": {"database": str(editing.database_path),
                          "document_id": "document", "operation_id": "finalize"},
    })
    queue.run_until_idle()
    first_result = queue.status(job["id"])["result"]
    assert first_result["mail"]["accepted"] is True
    assert queue.status(job["id"])["status"] == "failed"

    with editing.transaction("document", lambda: {}) as state:
        state["operations"]["finalize"]["status"] = "unknown"
    assert queue.retry(job["id"])["status"] == "held"
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET updated_at=? WHERE id=?",
                   (time.time() - 7200, job["id"]))
    queue.run_until_idle()
    expired = queue.status(job["id"])
    assert expired["status"] == "failed"
    assert expired["result"] == {**first_result, "commit_guard_required": True}

    with editing.transaction("document", lambda: {}) as state:
        state["operations"]["finalize"]["status"] = "complete"
    queue.retry(job["id"])
    queue.run_until_idle()
    assert queue.status(job["id"])["status"] == "complete"
    assert calls == ["mail"]
