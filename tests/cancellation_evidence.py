"""Valid synthetic cancellation at the transport boundary for generic queue tests.

The callback observes a conditional move, never a real remove-by-name. Tests of
the cancellation proof itself live in test_cancellation_recovery.py.
"""
import json
from types import SimpleNamespace

from tests.editing_evidence import committed_editing_repository


def ready_cancellation(queue, root, *, name="synthetic.xlsx", on_move=None):
    graph = queue.graph_service
    if not hasattr(graph, "config"):
        graph.config = SimpleNamespace(drive_id="synthetic", active_path="Activas", archive_path="Arquivadas")
    drive = graph.config.drive_id
    active_path = graph.config.active_path + "/" + name
    target_path = graph.config.archive_path + "/Canceladas"
    old_lookup = getattr(graph, "_get_item_by_path", lambda _path: None)
    old_bytes = getattr(graph, "_graph_bytes", None)
    def lookup(path):
        if path == active_path:
            return {"id": "synthetic-cancel-id", "eTag": "v1", "file": {}}
        if path == target_path:
            return {"id": "synthetic-cancel-parent", "folder": {}}
        return old_lookup(path)
    def transfer(method, path, *, data=None, content_type=None, headers=None):
        if path == f"/drives/{drive}/items/synthetic-cancel-id":
            assert method == "PATCH" and headers["If-Match"] == "v1"
            assert set(headers) <= {"If-Match", "client-request-id"}
            body = json.loads(data)
            assert body["parentReference"]["id"] == "synthetic-cancel-parent"
            assert body["@microsoft.graph.conflictBehavior"] == "fail"
            if on_move:
                on_move(name)
            return json.dumps({"id": "synthetic-cancel-id", "name": body["name"],
                               "parentReference": body["parentReference"]}).encode()
        if old_bytes is None:
            raise AssertionError("Unexpected synthetic transport")
        return old_bytes(method, path, data=data, content_type=content_type, headers=headers)
    graph._get_item_by_path = lookup
    graph._graph_bytes = transfer
    graph._join_graph_path = lambda *parts: "/".join(parts)
    graph.ensure_folder_path = lambda path: None if path == target_path else (_ for _ in ()).throw(AssertionError(path))
    editing = committed_editing_repository(root)
    return queue.enqueue("remove_active", {
        "_commit_guard": {"database": str(editing.database_path), "document_id": "document", "operation_id": "finalize"},
        "cancellation": {"format": 1, "drive_id": drive, "source_name": name, "destination_name": "cancel-" + "a" * 64,
                         "absent": False, "source_id": "synthetic-cancel-id", "source_etag": "v1", "folder": False, "children": []},
    })
