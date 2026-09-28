"""Refresh correlation across real processes; every Graph transport is synthetic."""

import json
import multiprocessing
import threading
import time
from pathlib import Path

from src.services.active_file_index import ACTIVE_INDEX_NAME
from src.services.graph_storage_service import GraphConfig, GraphStorageError, GraphStorageService
from src.services.graph_sync_coordinator import GraphRefreshCoordinator, REFRESH_STATE_NAME


def wait_finished(coordinator):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = coordinator.status()
        if not state["in_progress"]:
            return state
        time.sleep(0.01)
    raise AssertionError("Synthetic refresh did not complete")


class SyntheticGraph(GraphStorageService):
    def __init__(self):
        super().__init__(GraphConfig("synthetic", "synthetic", "synthetic", "synthetic"))

    def list_active_items(self):
        return []


def running_process(directory, started, release, replies):
    from tools.test_runtime import block_outbound_network
    block_outbound_network()
    thread_errors = []
    threading.excepthook = lambda error: thread_errors.append(repr(error.exc_value))

    class PausedGraph(SyntheticGraph):
        def list_active_items(self):
            started.set()
            if not release.wait(5):
                raise GraphStorageError("Synthetic release timed out")
            return []

    coordinator = GraphRefreshCoordinator(PausedGraph(), Path(directory))
    replies.put(coordinator.request_refresh(force=True))
    completed = wait_finished(coordinator)
    for worker in threading.enumerate():
        if worker.name == "graph-active-refresh":
            worker.join(timeout=2)
    completed["thread_errors"] = thread_errors
    replies.put(completed)


def test_running_refresh_has_same_identifier_and_result_in_other_process(tmp_path):
    context = multiprocessing.get_context("spawn")
    started, release, replies = context.Event(), context.Event(), context.Queue()
    process = context.Process(target=running_process, args=(str(tmp_path), started, release, replies))
    process.start()
    try:
        requested = replies.get(timeout=8)
        assert started.wait(2)
        observer = GraphRefreshCoordinator(SyntheticGraph(), tmp_path)
        observed = observer.status()
        duplicate = observer.request_refresh(force=True)
        assert requested["refresh_id"] == observed["refresh_id"] == duplicate["refresh_id"]
        assert requested["generation_id"] == observed["generation_id"] == duplicate["generation_id"]
        assert requested["refresh_sequence"] == observed["refresh_sequence"] == duplicate["refresh_sequence"] == 1
        assert observed["in_progress"] is True
        assert duplicate["in_progress"] is True
        assert observed["last_completed_refresh_id"] is None
        release.set()
        completed = replies.get(timeout=8)
        process.join(timeout=5)
        assert process.exitcode == 0
        persisted = GraphRefreshCoordinator(SyntheticGraph(), tmp_path).status()
        assert not completed["thread_errors"], completed["thread_errors"]
        assert persisted["outcome"] == completed["outcome"] == "success", completed
        assert persisted["last_completed_refresh_id"] == requested["refresh_id"]
        assert persisted["last_completed_sequence"] == requested["refresh_sequence"]
        assert persisted["inventory"]["refresh_id"] == requested["refresh_id"]
        assert persisted["inventory"]["available"] is True
        assert persisted["inventory"]["stale"] is False
        assert persisted["last_success_at"] == completed["last_success_at"]
        # Throttling also uses the persisted attempt from the other process.
        assert observer.request_refresh()["refresh_id"] == requested["refresh_id"]
    finally:
        release.set()
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        replies.close()


def test_concurrent_direct_refresh_cannot_publish_an_older_inventory(tmp_path):
    started, release = threading.Event(), threading.Event()
    failures = []

    class PausedGraph(SyntheticGraph):
        def list_active_items(self):
            started.set()
            release.wait(3)
            return []

    graph = PausedGraph()

    def refresh():
        try:
            graph.sync_active_files(tmp_path)
        except Exception as exc:
            failures.append(exc)

    worker = threading.Thread(target=refresh)
    worker.start()
    try:
        assert started.wait(1)
        from src.services.file_mutex import FileMutexBusy
        import pytest
        with pytest.raises(FileMutexBusy):
            SyntheticGraph().sync_active_files(tmp_path)
        assert not (tmp_path / ACTIVE_INDEX_NAME).exists()
    finally:
        release.set()
        worker.join(timeout=5)
    assert not failures
    assert json.loads((tmp_path / ACTIVE_INDEX_NAME).read_text())["files"] == []


def test_failed_refresh_preserves_confirmed_inventory_with_explicit_stale_status(tmp_path):
    graph = SyntheticGraph()
    coordinator = GraphRefreshCoordinator(graph, tmp_path)
    first = coordinator.request_refresh(force=True)
    successful = wait_finished(coordinator)
    original = (tmp_path / ACTIVE_INDEX_NAME).read_bytes()

    class FailingGraph(SyntheticGraph):
        def list_active_items(self):
            raise GraphStorageError("Synthetic private transport detail")

    coordinator = GraphRefreshCoordinator(FailingGraph(), tmp_path)
    second = coordinator.request_refresh(force=True)
    failed = wait_finished(coordinator)
    assert second["refresh_id"] != first["refresh_id"]
    assert failed["last_completed_refresh_id"] == second["refresh_id"]
    assert failed["outcome"] == failed["last_completed_outcome"] == "error"
    assert failed["last_error"] and "private" not in failed["last_error"]
    assert failed["last_success_at"] == successful["last_success_at"]
    assert failed["inventory"]["refresh_id"] == first["refresh_id"]
    assert failed["inventory"]["stale"] is True
    assert (tmp_path / ACTIVE_INDEX_NAME).read_bytes() == original


def test_interrupted_refresh_is_explicit_and_can_be_retried(tmp_path):
    coordinator = GraphRefreshCoordinator(SyntheticGraph(), tmp_path)
    state = coordinator._empty_state()
    state.update(in_progress=True, outcome="running", refresh_id="synthetic-interrupted", last_started_at=time.time())
    (tmp_path / REFRESH_STATE_NAME).write_text(json.dumps(state), encoding="utf-8")
    interrupted = coordinator.status()
    assert interrupted["in_progress"] is False
    assert interrupted["outcome"] == "error"
    assert interrupted["last_completed_refresh_id"] == "synthetic-interrupted"
    assert interrupted["last_error"]
    retried = coordinator.request_refresh(force=True)
    assert retried["refresh_id"] != "synthetic-interrupted"
    assert wait_finished(coordinator)["outcome"] == "success"


def test_missing_or_corrupt_inventory_has_explicit_unavailable_status(tmp_path):
    coordinator = GraphRefreshCoordinator(SyntheticGraph(), tmp_path)
    assert coordinator.status()["inventory"]["error"] == "missing"
    (tmp_path / ACTIVE_INDEX_NAME).write_text("broken", encoding="utf-8")
    inventory = coordinator.status()["inventory"]
    assert inventory["available"] is False
    assert inventory["error"] == "invalid"
    assert inventory["stale"] is True


def test_later_completion_proves_an_earlier_request_finished_in_the_same_generation(tmp_path):
    first_coordinator = GraphRefreshCoordinator(SyntheticGraph(), tmp_path)
    first = first_coordinator.request_refresh(force=True)
    wait_finished(first_coordinator)
    second_coordinator = GraphRefreshCoordinator(SyntheticGraph(), tmp_path)
    second = second_coordinator.request_refresh(force=True)
    wait_finished(second_coordinator)
    # Browser A polls only after browser B has also completed its request.
    observed = GraphRefreshCoordinator(SyntheticGraph(), tmp_path).status()
    assert observed["last_completed_refresh_id"] != first["refresh_id"]
    assert first["generation_id"] == second["generation_id"] == observed["generation_id"]
    assert first["refresh_sequence"] == 1
    assert second["refresh_sequence"] == observed["last_completed_sequence"] == 2
    assert observed["last_completed_sequence"] >= first["refresh_sequence"]


def test_state_loss_starts_a_new_generation_and_cannot_confirm_old_requests(tmp_path):
    coordinator = GraphRefreshCoordinator(SyntheticGraph(), tmp_path)
    old = coordinator.request_refresh(force=True)
    wait_finished(coordinator)
    (tmp_path / REFRESH_STATE_NAME).write_text("synthetic corrupted state", encoding="utf-8")
    unavailable = coordinator.status()
    assert unavailable["generation_id"] is None
    assert unavailable["last_completed_sequence"] == 0
    assert coordinator.status()["generation_id"] is None
    replacement = coordinator.request_refresh(force=True)
    completed = wait_finished(coordinator)
    assert completed["generation_id"] == replacement["generation_id"] != old["generation_id"]
    assert completed["last_completed_sequence"] == replacement["refresh_sequence"] == 1


def test_legacy_state_gets_a_persistent_generation_only_when_accepting_a_request(tmp_path):
    coordinator = GraphRefreshCoordinator(SyntheticGraph(), tmp_path)
    # The original v1 format has no generation or sequence fields.
    legacy = {key: value for key, value in coordinator._empty_state().items()
              if key not in {"generation_id", "refresh_sequence", "last_completed_sequence"}}
    (tmp_path / REFRESH_STATE_NAME).write_text(json.dumps(legacy), encoding="utf-8")
    assert coordinator.status()["generation_id"] is None
    assert coordinator.status()["generation_id"] is None
    accepted = coordinator.request_refresh(force=True)
    completed = wait_finished(coordinator)
    assert accepted["generation_id"]
    assert completed["generation_id"] == accepted["generation_id"]
    assert completed["last_completed_sequence"] == 1
    assert GraphRefreshCoordinator(SyntheticGraph(), tmp_path).status()["generation_id"] == accepted["generation_id"]


def test_interrupted_numbered_request_records_its_completed_sequence(tmp_path):
    coordinator = GraphRefreshCoordinator(SyntheticGraph(), tmp_path)
    state = coordinator._empty_state()
    state.update(in_progress=True, outcome="running", refresh_id="synthetic-interrupted",
                 generation_id="synthetic-generation", refresh_sequence=3, last_completed_sequence=2,
                 last_started_at=time.time())
    (tmp_path / REFRESH_STATE_NAME).write_text(json.dumps(state), encoding="utf-8")
    interrupted = coordinator.status()
    assert interrupted["outcome"] == "error"
    assert interrupted["generation_id"] == "synthetic-generation"
    assert interrupted["last_completed_sequence"] == 3
