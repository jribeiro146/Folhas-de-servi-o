"""Reject incompatible SQLite stores without interpreting or changing their data."""
import sqlite3
from contextlib import closing

import pytest

from src.services.runtime_contract import ContractError, RuntimeContract
from src.services.release_identity import get_release_info


def factory(kind, path):
    if kind == "editing":
        from src.services.editing_state_repository import SqliteStateRepository
        return SqliteStateRepository(path)
    from src.services.graph_sync_queue import GraphSyncQueue
    return GraphSyncQueue(None, path, auto_start=False)


@pytest.mark.parametrize("kind", ["editing", "queue"])
def test_new_store_gets_distinct_identity_and_version_and_reopens(tmp_path, kind):
    database = tmp_path / "state.sqlite3"
    factory(kind, database)
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("PRAGMA application_id").fetchone()[0] != 0
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
    factory(kind, database)


@pytest.mark.parametrize("kind", ["editing", "queue"])
@pytest.mark.parametrize("damage", ["future", "wrong_identity", "missing_column"])
def test_incompatible_marked_store_is_refused(tmp_path, kind, damage):
    database = tmp_path / "state.sqlite3"
    factory(kind, database)
    with closing(sqlite3.connect(database)) as db:
        if damage == "future":
            db.execute("PRAGMA user_version=99")
        elif damage == "wrong_identity":
            db.execute("PRAGMA application_id=1234")
        else:
            table, column = ("editing_states", "state_json") if kind == "editing" else ("graph_sync_jobs", "kind")
            db.execute(f"ALTER TABLE {table} RENAME COLUMN {column} TO unsupported")
        db.commit()
    with pytest.raises(ContractError):
        factory(kind, database)


@pytest.mark.parametrize("kind", ["editing", "queue"])
@pytest.mark.parametrize("table", ["unknown", "sqliteXcustom"])
def test_unknown_unmarked_store_is_not_treated_as_empty_or_switched_to_wal(tmp_path, kind, table):
    database = tmp_path / "unknown.sqlite3"
    with closing(sqlite3.connect(database)) as db:
        db.execute(f"CREATE TABLE {table}(value TEXT)")
        db.execute(f"INSERT INTO {table} VALUES ('synthetic retained data')")
        db.commit()
    with pytest.raises(ContractError):
        factory(kind, database)
    with closing(sqlite3.connect(database)) as db:
        assert db.execute(f"SELECT value FROM {table}").fetchall() == [("synthetic retained data",)]
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert db.execute("PRAGMA application_id").fetchone()[0] == 0
    assert not (tmp_path / "graph-sync-assets").exists()


def test_existing_instances_refuse_mutations_and_claims_after_format_changes(tmp_path):
    editing = factory("editing", tmp_path / "editing.sqlite3")
    queue = factory("queue", tmp_path / "queue.sqlite3")
    job = queue.enqueue("remove_active", {"source_name": "synthetic.xlsx"})
    for instance in (editing, queue):
        with closing(sqlite3.connect(instance.database_path)) as db:
            db.execute("PRAGMA user_version=99")
    with pytest.raises(ContractError):
        with editing.transaction("new", lambda: {}):
            pytest.fail("Unknown editing format admitted")
    with pytest.raises(ContractError):
        queue.run_until_idle()
    with pytest.raises(ContractError):
        queue.enqueue("remove_active", {"source_name": "other.xlsx"})
    with closing(sqlite3.connect(queue.database_path)) as db:
        assert db.execute("SELECT id,status,attempts FROM graph_sync_jobs").fetchall() == [(job["id"], "pending", 0)]


def test_web_preflights_unused_queue_before_creating_other_stores(tmp_path, monkeypatch):
    import src.web.application as web
    data = tmp_path / "data"
    contract = RuntimeContract(data, get_release_info()["source_sha256"])
    contract.initialize_empty()
    with closing(sqlite3.connect(data / "graph-sync.sqlite3")) as db:
        db.execute("PRAGMA user_version=99")
    monkeypatch.setattr(web, "APP_DATA_DIR", data)
    with pytest.raises(ContractError):
        web.create_app()
    assert not (data / "editing-state").exists()


@pytest.mark.parametrize("kind", ["editing", "queue"])
def test_corrupt_store_has_controlled_refusal_and_is_not_replaced(tmp_path, kind):
    database = tmp_path / "corrupt.sqlite3"
    database.write_bytes(b"not a database - synthetic recovery evidence")
    before = database.read_bytes()
    with pytest.raises(ContractError):
        factory(kind, database)
    assert database.read_bytes() == before


def test_future_editing_store_never_releases_held_external_work(tmp_path):
    editing = factory("editing", tmp_path / "editing.sqlite3")
    with editing.transaction("document", lambda: {}) as state:
        state["operations"] = {"send": {"status": "complete"}}
    queue = factory("queue", tmp_path / "queue.sqlite3")
    job = queue.enqueue("remove_active", {"source_name": "synthetic.xlsx", "_commit_guard": {
        "database": str(editing.database_path), "document_id": "document", "operation_id": "send"}})
    with closing(sqlite3.connect(editing.database_path)) as db:
        db.execute("PRAGMA user_version=99")
    queue.run_until_idle()
    assert queue.status(job["id"])["status"] == "held"
    assert queue.status(job["id"])["attempts"] == 0


def test_legacy_json_requires_preparation_before_database_creation(tmp_path):
    from src.services.editing_state_repository import SqliteStateRepository
    state = tmp_path / "old.json"
    original = '{"document_id":"synthetic","version":2,"revision":7}'
    state.write_text(original)
    with pytest.raises(ContractError):
        SqliteStateRepository(tmp_path / "editing.sqlite3", legacy_root=tmp_path)
    assert not (tmp_path / "editing.sqlite3").exists()
    assert state.read_text() == original


def _initialize_in_child(kind, path, ready, go, results):
    from tools.test_runtime import block_outbound_network
    block_outbound_network()
    ready.put(True)
    if not go.wait(10):
        raise RuntimeError("Synthetic initialization timed out")
    factory(kind, path)
    results.put("ok")


@pytest.mark.parametrize("kind", ["editing", "queue"])
def test_two_processes_initialize_one_store_without_partial_schema(tmp_path, kind):
    import multiprocessing
    context = multiprocessing.get_context("spawn")
    ready, results, go = context.Queue(), context.Queue(), context.Event()
    path = str(tmp_path / "shared.sqlite3")
    children = [context.Process(target=_initialize_in_child, args=(kind, path, ready, go, results)) for _ in range(2)]
    for child in children:
        child.start()
    try:
        assert ready.get(timeout=10) and ready.get(timeout=10)
        go.set()
        assert results.get(timeout=15) == results.get(timeout=15) == "ok"
        for child in children:
            child.join(5)
            assert child.exitcode == 0
        factory(kind, path)
    finally:
        go.set()
        for child in children:
            child.join(2)
            if child.is_alive():
                child.terminate()
                child.join(2)
        ready.close()
        results.close()


@pytest.mark.parametrize("kind,relative", [("editing", "editing-state/editing-state.sqlite3"), ("queue", "graph-sync.sqlite3")])
@pytest.mark.parametrize("uncommitted", [False, True])
def test_preflight_accepts_zero_byte_or_uncommitted_new_database(tmp_path, kind, relative, uncommitted):
    from src.services.sqlite_storage_format import preflight_stores, SCHEMAS
    database = tmp_path / relative
    database.parent.mkdir(parents=True, exist_ok=True)
    database.touch()
    with closing(sqlite3.connect(database)) as writer:
        if uncommitted:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute(SCHEMAS[kind][0])
        preflight_stores(tmp_path)
        writer.rollback()
    factory(kind, database)
    preflight_stores(tmp_path)


def test_zero_byte_editing_database_with_legacy_json_remains_unprepared(tmp_path):
    from src.services.sqlite_storage_format import preflight_stores
    from src.services.editing_state_repository import SqliteStateRepository
    directory = tmp_path / "editing-state"
    directory.mkdir()
    database = directory / "editing-state.sqlite3"
    database.touch()
    (directory / "old.json").write_text('{"document_id":"synthetic","revision":17}')
    with pytest.raises(ContractError):
        preflight_stores(tmp_path)
    with pytest.raises(ContractError):
        SqliteStateRepository(database, legacy_root=directory)
    assert database.read_bytes() == b""


def test_marked_editing_database_never_adopts_an_unimported_legacy_json(tmp_path):
    import hashlib
    import json
    from src.services.editing_state_service import EditingStateService
    service = EditingStateService(tmp_path)
    document = "synthetic-unimported"
    legacy = tmp_path / (hashlib.sha256(document.encode()).hexdigest() + ".json")
    original = json.dumps({"document_id": document, "version":99, "revision":17,
                           "autosave":{"private":"legacy evidence"}, "sessions":{}, "owner":None})
    legacy.write_text(original)
    service.snapshot_document(document)
    with closing(sqlite3.connect(service.repository.database_path)) as db:
        stored = json.loads(db.execute("SELECT state_json FROM editing_states WHERE document_id=?", (document,)).fetchone()[0])
    assert stored["revision"] == 1
    assert stored["autosave"] is None
    assert legacy.read_text() == original


def _start_operational_entry(entry, directory, ready, go, results):
    import os
    from tools.test_runtime import block_outbound_network
    block_outbound_network()
    os.environ["FS_APP_DATA_DIR"] = directory
    ready.put(True)
    if not go.wait(10):
        raise RuntimeError("Synthetic startup timed out")
    if entry == "web":
        from src.web.application import app
        assert app.test_client().get("/").status_code == 200
    else:
        from src.queue_worker import build_queue
        build_queue()
    results.put("ok")


def test_web_and_worker_start_together_with_preflight_on_new_storage(tmp_path):
    import multiprocessing
    RuntimeContract(tmp_path, get_release_info()["source_sha256"]).initialize_empty()
    context = multiprocessing.get_context("spawn")
    ready, results, go = context.Queue(), context.Queue(), context.Event()
    children = [context.Process(target=_start_operational_entry, args=(entry, str(tmp_path), ready, go, results))
                for entry in ("web", "worker")]
    for child in children:
        child.start()
    try:
        assert ready.get(timeout=10) and ready.get(timeout=10)
        go.set()
        assert results.get(timeout=30) == results.get(timeout=30) == "ok"
        for child in children:
            child.join(5)
            assert child.exitcode == 0
        factory("editing", tmp_path / "editing-state" / "editing-state.sqlite3")
        factory("queue", tmp_path / "graph-sync.sqlite3")
    finally:
        go.set()
        for child in children:
            child.join(2)
            if child.is_alive():
                child.terminate()
                child.join(2)
        ready.close()
        results.close()


def test_marked_store_with_unknown_check_constraint_is_not_compatible(tmp_path):
    database = tmp_path / "editing.sqlite3"
    factory("editing", database)
    with closing(sqlite3.connect(database)) as db:
        ddl = db.execute("SELECT sql FROM sqlite_master WHERE name='editing_states'").fetchone()[0]
        db.execute("DROP TABLE editing_states")
        db.execute(ddl.replace("state_json TEXT NOT NULL", "state_json TEXT NOT NULL CHECK(length(state_json)<4)"))
        db.commit()
    with pytest.raises(ContractError):
        factory("editing", database)
