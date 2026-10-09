"""Cancellation keeps recoverable copies and never targets a name on retry."""
import json
import time
from pathlib import Path

import pytest

from src.services.graph_storage_service import GraphConfig, GraphStorageService, GraphConflictError
from src.services.graph_storage_service import GraphStorageError
from src.services.graph_sync_queue import GraphSyncQueue
from tests.editing_evidence import committed_editing_repository


class CancellationGraph(GraphStorageService):
    def __init__(self):
        super().__init__(GraphConfig("synthetic", "synthetic", "synthetic", "drive"))
        self.item = {"id": "original", "name": "draft", "eTag": "folder-v1", "folder": {"childCount": 1}}
        self.children = [{"id": "book", "name": "draft.xlsx", "eTag": "book-v1", "file": {"mimeType": "excel"}}]
        self.writes = []
        self.lost_response = False

    def _get_item_by_path(self, path):
        if path == "Activas/draft":
            return self.item
        if path == "Arquivadas/Canceladas":
            return {"id": "canceled-root", "folder": {"childCount": 0}}
        return None

    def _list_children(self, item_id):
        assert item_id == "original"
        return list(self.children)

    def ensure_folder_path(self, path):
        assert path == "Arquivadas/Canceladas"

    def _graph_bytes(self, method, path, data=None, content_type=None, headers=None):
        assert method == "PATCH", "Cancellation must preserve the remote bundle, never recursively delete it"
        assert path == "/drives/drive/items/original"
        assert headers == {"If-Match": "folder-v1"}
        body = json.loads(data)
        assert body["parentReference"] == {"id": "canceled-root"}
        self.writes.append(body)
        result = {"id": "original", "name": body["name"], "parentReference": {"id": "canceled-root"}}
        self.item = {"id": "replacement", "name": "draft", "eTag": "new-folder", "folder": {}}
        if self.lost_response:
            raise TimeoutError("Synthetic response lost after move")
        return json.dumps(result).encode()


def source_with_receipts(root):
    folder = root / "draft"
    folder.mkdir()
    source = folder / "draft.xlsx"
    source.write_bytes(b"synthetic workbook")
    GraphStorageService._write_bundle_meta(folder, {"id": "original", "eTag": "folder-v1"})
    GraphStorageService._write_item_meta(GraphStorageService._item_meta_path(source), {"id": "book", "eTag": "book-v1"})
    return source


def plan(graph, source):
    from src.services.graph_cancellation import prepare_cancellation
    return prepare_cancellation(graph, source, "synthetic-document:cancel-1")


def guarded_payload(tmp_path, remote_plan):
    editing = committed_editing_repository(tmp_path)
    return editing, {"cancellation": remote_plan, "_commit_guard": {
        "database": str(editing.database_path), "document_id": "document", "operation_id": "finalize"}}


def test_cancellation_moves_only_confirmed_id_and_preserves_children(tmp_path):
    from src.services.graph_cancellation import execute_cancellation
    graph = CancellationGraph()
    source = source_with_receipts(tmp_path)
    remote_plan = plan(graph, source)
    result = execute_cancellation(graph, remote_plan)
    assert result["removed_active"] is True and result["remote_preserved"] is True
    assert len(graph.writes) == 1 and source.read_bytes() == b"synthetic workbook"


@pytest.mark.parametrize("change", ["homonym", "etag", "child", "new_child", "missing_etag"])
def test_changed_remote_proof_is_refused_before_any_write(tmp_path, change):
    from src.services.graph_cancellation import execute_cancellation
    graph = CancellationGraph()
    remote_plan = plan(graph, source_with_receipts(tmp_path))
    if change == "homonym":
        graph.item["id"] = "replacement"
    elif change == "etag":
        graph.item["eTag"] = "changed"
    elif change == "child":
        graph.children[0]["eTag"] = "changed-child"
    elif change == "new_child":
        graph.children.append({"id": "someone-else", "name": "extra.xlsx", "eTag": "other", "file": {}})
    else:
        graph.item.pop("eTag")
    with pytest.raises(GraphConflictError):
        execute_cancellation(graph, remote_plan)
    assert graph.writes == []


def test_never_published_draft_does_not_touch_a_later_homonym(tmp_path):
    from src.services.graph_cancellation import execute_cancellation
    graph = CancellationGraph()
    source = source_with_receipts(tmp_path)
    graph.item = None
    remote_plan = plan(graph, source)
    graph.item = {"id": "later-homonym", "eTag": "new", "folder": {}}
    assert execute_cancellation(graph, remote_plan)["removed_active"] is False
    assert graph.writes == []


@pytest.mark.parametrize("receipt", ["missing", "wrong_id", "wrong_child", "malformed"])
def test_cancellation_requires_local_ownership_and_file_versions(tmp_path, receipt):
    graph = CancellationGraph()
    source = source_with_receipts(tmp_path)
    meta = source.parent / ".graph_bundle.json"
    if receipt == "missing":
        meta.unlink()
    elif receipt == "wrong_id":
        meta.write_text('{"id":"someone-else","eTag":"folder-v1"}')
    elif receipt == "wrong_child":
        GraphStorageService._item_meta_path(source).write_text('{"id":"other","eTag":"book-v1"}')
    else:
        meta.write_text("{broken")
    with pytest.raises(GraphConflictError):
        plan(graph, source)
    assert graph.writes == []


def test_queue_waits_for_local_commit_then_executes_once(tmp_path):
    graph = CancellationGraph()
    editing, payload = guarded_payload(tmp_path, plan(graph, source_with_receipts(tmp_path)))
    with editing.transaction("document", lambda: {}) as state:
        state["operations"]["finalize"]["status"] = "pending"
    queue = GraphSyncQueue(graph, tmp_path / "queue.sqlite3", auto_start=False)
    job = queue.enqueue("remove_active", payload)
    queue.run_until_idle()
    assert queue.status(job["id"])["status"] == "held" and not graph.writes
    with editing.transaction("document", lambda: {}) as state:
        state["operations"]["finalize"]["status"] = "complete"
    queue.run_until_idle()
    assert queue.status(job["id"])["status"] == "complete"
    queue.run_until_idle()
    assert len(graph.writes) == 1


def test_lost_response_blocks_manual_and_automatic_repetition(tmp_path):
    graph = CancellationGraph()
    graph.lost_response = True
    _, payload = guarded_payload(tmp_path, plan(graph, source_with_receipts(tmp_path)))
    queue = GraphSyncQueue(graph, tmp_path / "queue.sqlite3", auto_start=False)
    job = queue.enqueue("remove_active", payload)
    queue.run_until_idle()
    result = queue.status(job["id"])
    assert result["status"] == "failed" and result["reconciliation_required"]
    assert not result["will_retry"]
    with pytest.raises(ValueError):
        queue.retry(job["id"])
    GraphSyncQueue(graph, queue.database_path, auto_start=False).run_until_idle()
    assert len(graph.writes) == 1 and graph.item["id"] == "replacement"


@pytest.mark.parametrize("boundary", ["lookup", "children", "destination"])
def test_failure_before_patch_remains_retryable(tmp_path, boundary):
    graph = CancellationGraph()
    _, payload = guarded_payload(tmp_path, plan(graph, source_with_receipts(tmp_path)))
    method = {"lookup": "_get_item_by_path", "children": "_list_children", "destination": "ensure_folder_path"}[boundary]
    original = getattr(graph, method)
    failed = []
    def fail_once(*args, **kwargs):
        if not failed:
            failed.append(True)
            raise GraphStorageError("Synthetic transient preflight failure")
        return original(*args, **kwargs)
    setattr(graph, method, fail_once)
    queue = GraphSyncQueue(graph, tmp_path / "queue.sqlite3", auto_start=False)
    job = queue.enqueue("remove_active", payload)
    queue.run_until_idle()
    result = queue.status(job["id"])
    assert result["status"] == "failed" and result["will_retry"]
    assert not result["reconciliation_required"] and not graph.writes
    with queue._connect() as connection:
        connection.execute("UPDATE graph_sync_jobs SET next_attempt_at=0 WHERE id=?", (job["id"],))
    queue.run_until_idle()
    assert queue.status(job["id"])["status"] == "complete" and len(graph.writes) == 1


@pytest.mark.parametrize("status_code", [409, 412])
def test_explicit_patch_rejection_is_terminal_but_not_uncertain(tmp_path, status_code):
    graph = CancellationGraph()
    _, payload = guarded_payload(tmp_path, plan(graph, source_with_receipts(tmp_path)))
    calls = []
    def rejected(*args, **kwargs):
        calls.append(args)
        raise GraphConflictError("Synthetic explicit refusal", status_code=status_code)
    graph._graph_bytes = rejected
    queue = GraphSyncQueue(graph, tmp_path / "queue.sqlite3", auto_start=False)
    job = queue.enqueue("remove_active", payload)
    queue.run_until_idle()
    result = queue.status(job["id"])
    assert result["status"] == "failed" and not result["will_retry"]
    assert not result["reconciliation_required"]
    assert result["result"]["phase"] == "cancellation_rejected"
    queue.run_until_idle()
    assert len(calls) == 1


def test_legacy_cancellation_is_not_scheduled_for_new_cleanup(tmp_path):
    from src.services.editing_state_service import EditingStateService
    repository = committed_editing_repository(tmp_path)
    with repository.transaction("document", lambda: {}) as state:
        operation = state["operations"]["finalize"]
        operation["kind"] = "cancel"
        operation["context"] = {"source_path": str(tmp_path / "absent.xlsx"), "canceled_path": str(tmp_path / "old.xlsx")}
    editing = EditingStateService(tmp_path / "editing")
    with repository._connect() as connection:
        before = connection.execute("SELECT state_json FROM editing_states WHERE document_id='document'").fetchone()[0]
    assert list(editing.finalizations_to_retire()) == []
    assert list(editing.finalizations_to_retire()) == []
    with repository._connect() as connection:
        assert connection.execute("SELECT state_json FROM editing_states WHERE document_id='document'").fetchone()[0] == before


@pytest.mark.parametrize("confirmed", [False, True])
def test_restart_after_remote_checkpoint_never_replays_move(tmp_path, confirmed):
    graph = CancellationGraph()
    _, payload = guarded_payload(tmp_path, plan(graph, source_with_receipts(tmp_path)))
    queue = GraphSyncQueue(graph, tmp_path / "queue.sqlite3", auto_start=False)
    job = queue.enqueue("remove_active", payload)
    progress = {"phase": "cancellation_confirmed" if confirmed else "canceling_remote",
                "cancellation_started": True}
    if confirmed:
        progress.update(cancellation_confirmed=True, removed_active=True, remote_preserved=True)
    with queue._connect() as connection:
        connection.execute("UPDATE graph_sync_jobs SET status='running',attempts=1,updated_at=?,result_json=? WHERE id=?",
                           (time.time() - 2000, json.dumps(progress), job["id"]))
    queue.run_until_idle()
    result = queue.status(job["id"])
    assert result["status"] == ("complete" if confirmed else "failed")
    assert result["reconciliation_required"] is (not confirmed)
    assert not graph.writes


def test_uncertain_cancel_with_different_stale_deadlines_does_not_block_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("FS_MAIL_JOB_STALE_SECONDS", "1800")
    monkeypatch.setenv("FS_GRAPH_JOB_STALE_SECONDS", "900")
    graph = CancellationGraph()
    _, payload = guarded_payload(tmp_path, plan(graph, source_with_receipts(tmp_path)))
    queue = GraphSyncQueue(graph, tmp_path / "queue.sqlite3", auto_start=False)
    uncertain = queue.enqueue("remove_active", payload)
    following = queue.enqueue("remove_active", {
        **payload, "cancellation": {**payload["cancellation"], "absent": True}})
    with queue._connect() as connection:
        connection.execute(
            "UPDATE graph_sync_jobs SET status='running', attempts=1, updated_at=?, result_json=? WHERE id=?",
            (time.time() - 1000, json.dumps({"cancellation_started": True}), uncertain["id"]))
    queue.run_until_idle()
    result = queue.status(uncertain["id"])
    assert result["status"] == "failed" and result["reconciliation_required"]
    assert not result["will_retry"]
    assert queue.status(following["id"])["status"] == "complete"
    with pytest.raises(ValueError):
        queue.retry(uncertain["id"])
    GraphSyncQueue(graph, queue.database_path, auto_start=False).run_until_idle()
    assert queue.status(uncertain["id"])["status"] == "failed"
    assert not graph.writes


def test_legacy_remove_without_proof_cannot_delete_by_name(tmp_path):
    graph = CancellationGraph()
    queue = GraphSyncQueue(graph, tmp_path / "queue.sqlite3", auto_start=False)
    job = queue.enqueue("remove_active", {"source_name": "draft", "expected_etag": "folder-v1"})
    queue.run_until_idle()
    result = queue.status(job["id"])
    assert result["status"] == "failed" and result["result"]["invalid_state"]
    assert not result["will_retry"] and not graph.writes


@pytest.fixture
def cancellation_http(tmp_path, monkeypatch):
    import shutil
    import src.web.application as web
    from src.services.file_service import FileService
    from src.services.editing_state_service import EditingStateService
    monkeypatch.setattr(web, "ACTIVE_AUTH_PROVIDER", "none")
    monkeypatch.setattr(web, "AUTH_ENABLED", False)
    monkeypatch.setattr(web, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(web, "APP_DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(web, "EXCEL_CANCELADAS_DIR", tmp_path / "Canceladas")
    from src.services.runtime_admission import resolve_runtime
    resolve_runtime(tmp_path / "data")[1].initialize_empty()
    active = tmp_path / "Activas"
    active.mkdir()
    source = active / "synthetic.xlsx"
    shutil.copy2(Path(__file__).parent / "fixtures/test_sample.xlsx", source)
    files = FileService(active, require_remote_index=False)
    draft = files.create_draft_copy(source, "Synthetic")
    graph = CancellationGraph()
    graph._get_item_by_path = lambda _path: None  # this synthetic draft was never published
    queue = GraphSyncQueue(graph, tmp_path / "queue.sqlite3", auto_start=False)
    editing = EditingStateService(tmp_path / "editing")
    app = web.create_app(file_service=files, graph_service=graph, graph_sync_queue=queue,
                         editing_state_service=editing)
    client = app.test_client()
    lease = client.post(f"/api/file/{draft.stem}/lease", json={"client_id": "cancel-tab"})
    assert lease.status_code == 200, lease.json
    state = lease.json["editing"]
    body = {"_edit": {"document_id": state["document_id"], "client_id": "cancel-tab",
             "lease_token": state["lease"]["token"], "base_revision": state["revision"], "idempotency_key": "cancel-1"}}
    return client, editing, queue, draft, body, tmp_path


@pytest.mark.parametrize("boundary", ["context", "publish_reply", "commit"])
def test_cancel_http_recovers_without_losing_source_or_duplicating_copy(cancellation_http, monkeypatch, boundary):
    from src.services.finalization_service import FinalizationService
    client, editing, queue, source, body, root = cancellation_http
    document = body["_edit"]["document_id"]
    contents = source.read_bytes()
    target = editing if boundary != "publish_reply" else FinalizationService
    method = {"context": "update_operation_context", "publish_reply": "publish", "commit": "commit_operation"}[boundary]
    original = getattr(target, method)
    fired = []
    def failure(*args, **kwargs):
        if fired:
            return original(*args, **kwargs)
        fired.append(True)
        if boundary == "publish_reply":
            original(*args, **kwargs)
        if boundary == "commit":
            queue.run_until_idle()
            assert queue.status(f"cancel:{document}:cancel-1")["status"] == "held"
        raise OSError("Synthetic interruption")
    monkeypatch.setattr(target, method, failure)
    failed = client.post(f"/api/file/{source.stem}/cancel", json=body)
    assert failed.status_code == 500
    assert source.read_bytes() == contents
    queue.run_until_idle()
    job = queue.status(f"cancel:{document}:cancel-1")
    assert job is None or job["status"] == "held"
    response = client.post(f"/api/file/{source.stem}/cancel", json=body)
    assert response.status_code == 200, response.json
    context = editing.operation(document, "cancel-1")["context"]
    assert Path(context["canceled_path"]).read_bytes() == contents
    assert not source.exists()
    assert len(list((root / "Canceladas").rglob("*.xlsx"))) == 1
    queue.run_until_idle()
    assert queue.status(response.json["graph_job_id"])["status"] == "complete"
    assert client.post(f"/api/file/{source.stem}/cancel", json=body).json == response.json


def test_committed_cancel_retirement_is_recovered_on_next_read(cancellation_http, monkeypatch):
    from src.services.finalization_service import FinalizationService
    client, editing, queue, source, body, _ = cancellation_http
    original = FinalizationService.retire_source
    def fail(*_args):
        raise OSError("Synthetic locked source")
    monkeypatch.setattr(FinalizationService, "retire_source", fail)
    response = client.post(f"/api/file/{source.stem}/cancel", json=body)
    assert response.status_code == 200 and source.exists()
    monkeypatch.setattr(FinalizationService, "retire_source", original)
    client.get(f"/api/file/{source.stem}/bootstrap?client_id=cancel-tab")
    assert not source.exists()
    assert editing.operation(body["_edit"]["document_id"], "cancel-1")["context"]["source_retired"]


def test_unfinished_upload_prevents_cancel_and_preserves_all_data(cancellation_http):
    client, editing, queue, source, body, root = cancellation_http
    upload = queue.enqueue("upload_active", {"draft_path": str(source)})
    response = client.post(f"/api/file/{source.stem}/cancel", json=body)
    assert response.status_code == 409 and source.exists()
    assert editing.operation(body["_edit"]["document_id"], "cancel-1") is None
    assert queue.status(upload["id"])["status"] == "pending"
    assert not list((root / "Canceladas").glob("*"))


def published_http_graph(queue, source, client, body):
    graph = queue.graph_service
    graph.item["name"] = source.parent.name
    graph.children[0]["name"] = source.name
    graph._get_item_by_path = lambda path: (graph.item if path == "Activas/" + source.parent.name else
        {"id": "canceled-root", "folder": {}} if path == "Arquivadas/Canceladas" else None)
    GraphStorageService._write_bundle_meta(source.parent, graph.item)
    GraphStorageService._write_item_meta(GraphStorageService._item_meta_path(source), graph.children[0])
    # This fixture adds receipts to an unassociated local draft. Obtain the
    # Graph-derived identity and current revision through the public lease API.
    lease = client.post(f"/api/file/{source.stem}/lease", json={"client_id": "cancel-tab"})
    assert lease.status_code == 200, lease.json
    current = lease.json["editing"]
    body["_edit"].update(document_id=current["document_id"], base_revision=current["revision"],
                         lease_token=current["lease"]["token"])
    return graph


def test_published_http_cancel_persists_proof_before_copy_and_queue(cancellation_http, monkeypatch):
    from src.services.finalization_service import FinalizationService
    client, editing, queue, source, body, _ = cancellation_http
    graph = published_http_graph(queue, source, client, body)
    original = FinalizationService.publish
    checks = []
    def checked_publish(self, staged, target, operation_id):
        context = editing.operation(body["_edit"]["document_id"], "cancel-1")["context"]
        assert context["remote_cancellation"]["source_id"] == "original"
        assert context["remote_cancellation"]["children"][0]["id"] == "book"
        assert context["canceled_path"] == str(target)
        checks.append("proof-before-copy")
        return original(self, staged, target, operation_id)
    monkeypatch.setattr(FinalizationService, "publish", checked_publish)
    response = client.post(f"/api/file/{source.stem}/cancel", json=body)
    assert response.status_code == 200, response.json
    job = queue.status(response.json["graph_job_id"])
    assert job["status"] == "held" and not graph.writes
    assert job["payload"]["cancellation"]["source_id"] == "original"
    queue.run_until_idle()
    assert queue.status(job["id"])["status"] == "complete"
    assert checks == ["proof-before-copy"] and len(graph.writes) == 1


def test_unmanaged_remote_photo_without_receipt_prevents_http_cancel(cancellation_http):
    client, editing, queue, source, body, root = cancellation_http
    graph = published_http_graph(queue, source, client, body)
    graph.children.append({"id": "photos", "name": "fotografias", "eTag": "p1", "folder": {}})
    graph._list_children = lambda item_id: (graph.children if item_id == "original" else
        [{"id": "other-photo", "name": "foreign.jpg", "eTag": "v1", "file": {}}])
    response = client.post(f"/api/file/{source.stem}/cancel", json=body)
    assert response.status_code == 409 and response.json["code"] == "graph_conflict"
    assert source.exists() and not graph.writes
    assert not list((root / "Canceladas").glob("*"))
