"""Release admission at the real web and background-operation boundaries."""
from pathlib import Path

import pytest

from src.services.release_identity import get_release_info
from src.services.runtime_contract import RuntimeContract, ContractError, ContractBusy, SUPPORTED_FORMATS


def test_web_holds_admission_during_request_and_refuses_old_release_after_activation(tmp_path):
    from src.web.application import create_app
    info = get_release_info()
    contract = RuntimeContract(tmp_path / "data", info["source_sha256"])
    contract.initialize_empty()
    application = create_app(runtime_contract=contract)
    writes = []

    @application.post("/synthetic-write")
    def synthetic_write():
        with pytest.raises(ContractBusy):
            contract.activate("b" * 64, next_formats=SUPPORTED_FORMATS)
        writes.append("saved")
        return {"success": True}

    client = application.test_client()
    assert client.post("/synthetic-write").status_code == 200
    contract.activate("b" * 64, next_formats=SUPPORTED_FORMATS)
    response = client.post("/synthetic-write")
    assert response.status_code == 503
    assert response.json["code"] == "state_contract_invalid"
    assert response.headers["Retry-After"] == "2"
    assert writes == ["saved"]


def test_web_refuses_unprepared_storage_before_initializing_services(tmp_path):
    from src.web.application import create_app
    contract = RuntimeContract(tmp_path / "missing", get_release_info()["source_sha256"])
    with pytest.raises(ContractError):
        create_app(runtime_contract=contract)
    assert not contract.data_dir.exists()


def test_http_error_also_releases_admission(tmp_path):
    from src.web.application import create_app
    contract = RuntimeContract(tmp_path / "data", get_release_info()["source_sha256"])
    contract.initialize_empty()
    application = create_app(runtime_contract=contract)

    @application.get("/synthetic-error")
    def synthetic_error():
        raise ValueError("synthetic failure")

    assert application.test_client().get("/synthetic-error").status_code == 500
    contract.activate("b" * 64, next_formats=SUPPORTED_FORMATS)


def test_data_inside_code_is_refused_before_release_inspection(tmp_path):
    from src.services.runtime_admission import resolve_runtime
    source = tmp_path / "src"
    source.mkdir()
    (source / "entry.py").write_text("synthetic=1\n", encoding="utf-8")
    data = source / "private-data"
    with pytest.raises(ContractError, match="dados.*código"):
        resolve_runtime(data, release_root=tmp_path)
    assert not data.exists()


def test_queue_refuses_stale_mutations_before_claim_or_external_call(tmp_path):
    from src.services.graph_sync_queue import GraphSyncQueue
    contract = RuntimeContract(tmp_path / "data", "a" * 64)
    contract.initialize_empty()
    queue = GraphSyncQueue(None, contract.data_dir / "queue.sqlite3", auto_start=False,
                           runtime_contract=contract)
    job = queue.enqueue("remove_active", {"source_name": "synthetic.xlsx"})
    contract.activate("b" * 64, next_formats=SUPPORTED_FORMATS)
    for operation in (queue.run_until_idle, lambda: queue.retry(job["id"]),
                      lambda: queue.enqueue("remove_active", {"source_name": "other.xlsx"})):
        with pytest.raises(ContractError):
            operation()
    assert queue.status(job["id"])["status"] == "pending"
    assert queue.status(job["id"])["attempts"] == 0
    assert not queue._worker_running


def test_queue_holds_admission_through_external_operation_and_completion(tmp_path):
    from src.services.graph_sync_queue import GraphSyncQueue
    contract = RuntimeContract(tmp_path / "data", "a" * 64)
    contract.initialize_empty()
    calls = []

    class Graph:
        def remove_active_name(self, name, **kwargs):
            with pytest.raises(ContractBusy):
                contract.activate("b" * 64, next_formats=SUPPORTED_FORMATS)
            calls.append(name)
            return True

    queue = GraphSyncQueue(Graph(), contract.data_dir / "queue.sqlite3", auto_start=False,
                           runtime_contract=contract)
    job = queue.enqueue("remove_active", {"source_name": "synthetic.xlsx"})
    assert queue.run_until_idle()
    assert queue.status(job["id"])["status"] == "complete"
    assert calls == ["synthetic.xlsx"]
    contract.activate("b" * 64, next_formats=SUPPORTED_FORMATS)


def test_refresh_holds_own_admission_after_request_returns(tmp_path):
    import threading
    from tests.test_graph_refresh_coordinator import SyntheticGraph, wait_finished
    from src.services.graph_sync_coordinator import GraphRefreshCoordinator
    contract = RuntimeContract(tmp_path / "data", "a" * 64)
    contract.initialize_empty()
    started, release = threading.Event(), threading.Event()

    class Graph(SyntheticGraph):
        def list_active_items(self):
            started.set()
            assert release.wait(5)
            return []

    coordinator = GraphRefreshCoordinator(Graph(), contract.data_dir / "active", runtime_contract=contract)
    coordinator.request_refresh(force=True)
    try:
        assert started.wait(3)
        with pytest.raises(ContractBusy):
            contract.activate("b" * 64, next_formats=SUPPORTED_FORMATS)
    finally:
        release.set()
    assert wait_finished(coordinator)["outcome"] == "success"
    contract.activate("b" * 64, next_formats=SUPPORTED_FORMATS)
    for operation in (coordinator.status, lambda: coordinator.request_refresh(force=True)):
        with pytest.raises(ContractError):
            operation()


def test_worker_factory_refuses_unprepared_storage_and_old_cycle(tmp_path, monkeypatch):
    import src.queue_worker as worker
    data = tmp_path / "data"
    monkeypatch.setattr(worker, "APP_DATA_DIR", data)
    with pytest.raises(ContractError):
        worker.build_queue()
    assert not data.exists()
    contract = RuntimeContract(data, get_release_info()["source_sha256"])
    contract.initialize_empty()
    queue = worker.build_queue()
    assert worker.process_once(queue)["worker_acquired"] is True
    contract.activate("b" * 64, next_formats=SUPPORTED_FORMATS)
    with pytest.raises(ContractError):
        worker.process_once(queue)


def test_refresh_waiting_to_start_refuses_old_code_and_releases_lock(tmp_path, monkeypatch):
    import threading
    from src.services.file_mutex import file_mutex
    from src.services.graph_sync_coordinator import GraphRefreshCoordinator, REFRESH_STATE_NAME
    from tests.test_graph_refresh_coordinator import SyntheticGraph
    contract = RuntimeContract(tmp_path / "data", "a" * 64)
    contract.initialize_empty()
    calls = []

    class Graph(SyntheticGraph):
        def list_active_items(self):
            calls.append("called")
            return []

    # Delay only the scheduling boundary. Execute the actual worker target after
    # activation, proving admission is acquired by it, not inherited from HTTP.
    pending = []
    monkeypatch.setattr(threading.Thread, "start", lambda self: pending.append(self))
    active = contract.data_dir / "active"
    coordinator = GraphRefreshCoordinator(Graph(), active, runtime_contract=contract)
    coordinator.request_refresh(force=True)
    before = (active / REFRESH_STATE_NAME).read_bytes()
    contract.activate("b" * 64, next_formats=SUPPORTED_FORMATS)
    pending.pop().run()
    assert calls == []
    assert (active / REFRESH_STATE_NAME).read_bytes() == before
    with file_mutex(active / ".graph_refresh_run.lock"):
        pass
    current = RuntimeContract(contract.data_dir, "b" * 64)
    observer = GraphRefreshCoordinator(Graph(), active, runtime_contract=current)
    assert observer.status()["outcome"] == "error"


def test_old_heartbeat_cannot_update_queue_after_activation(tmp_path):
    from src.services.graph_sync_queue import GraphSyncQueue
    contract = RuntimeContract(tmp_path / "data", "a" * 64)
    contract.initialize_empty()
    queue = GraphSyncQueue(None, contract.data_dir / "queue.sqlite3", auto_start=False,
                           runtime_contract=contract)
    queued = queue.enqueue("remove_active", {"source_name": "synthetic.xlsx"})
    with contract.operation():
        job = queue._claim_next()
    before = queue.status(queued["id"])
    contract.activate("b" * 64, next_formats=SUPPORTED_FORMATS)
    with pytest.raises(ContractError):
        queue._heartbeat(job)
    assert queue.status(queued["id"]) == before


def test_queue_initialization_refuses_old_release_without_creating_directories(tmp_path):
    from src.services.graph_sync_queue import GraphSyncQueue
    contract = RuntimeContract(tmp_path / "data", "a" * 64)
    contract.initialize_empty()
    contract.activate("b" * 64, next_formats=SUPPORTED_FORMATS)
    destination = tmp_path / "never-created"
    with pytest.raises(ContractError):
        GraphSyncQueue(None, destination / "queue.sqlite3", runtime_contract=contract)
    assert not destination.exists()


def test_additional_storage_paths_inside_code_are_refused(tmp_path):
    from src.services.runtime_admission import resolve_runtime
    source = tmp_path / "src"
    source.mkdir()
    for storage in (source / "cache", source / "logs", source / "excel"):
        with pytest.raises(ContractError, match="dados.*código"):
            resolve_runtime(tmp_path / "data", release_root=tmp_path, storage_paths=(storage,))


def test_admitted_http_can_enqueue_while_an_exclusive_writer_waits(tmp_path):
    import sqlite3
    import threading
    import time
    from src.web.application import create_app
    from src.services.graph_sync_queue import GraphSyncQueue
    contract = RuntimeContract(tmp_path / "data", get_release_info()["source_sha256"])
    contract.initialize_empty()
    queue = GraphSyncQueue(None, contract.data_dir / "queue.sqlite3", auto_start=False,
                           runtime_contract=contract)
    application = create_app(runtime_contract=contract)
    writer_outcome = []

    def writer():
        # A longer SQLite timeout makes PENDING deterministic; no data is changed.
        db = sqlite3.connect(contract.path, timeout=1)
        try:
            db.execute("BEGIN EXCLUSIVE")
            writer_outcome.append("acquired")
        except sqlite3.OperationalError:
            writer_outcome.append("busy")
        finally:
            db.close()

    @application.post("/synthetic-pending")
    def enqueue_during_pending():
        thread = threading.Thread(target=writer)
        thread.start()
        try:
            deadline = time.monotonic() + .8
            while True:
                probe = sqlite3.connect(contract.path, timeout=0)
                try:
                    probe.execute("SELECT epoch FROM state_contract").fetchone()
                except sqlite3.OperationalError:
                    break
                finally:
                    probe.close()
                assert time.monotonic() < deadline
                time.sleep(.005)
            queued = queue.enqueue("remove_active", {"source_name": "synthetic.xlsx"})
            return {"job_id": queued["id"]}
        finally:
            thread.join(2)

    response = application.test_client().post("/synthetic-pending")
    assert response.status_code == 200
    assert queue.status(response.json["job_id"])["status"] == "pending"
    assert writer_outcome == ["busy"]
    contract.activate("b" * 64, next_formats=SUPPORTED_FORMATS)


def _busy_once(monkeypatch, contract, *, thread_name=None):
    from contextlib import contextmanager
    import threading
    original = contract.operation
    failures = []

    @contextmanager
    def admission():
        if not failures and (thread_name is None or threading.current_thread().name == thread_name):
            failures.append("busy")
            raise ContractBusy("Synthetic transient SQLite lock")
        with original() as state:
            yield state
    monkeypatch.setattr(contract, "operation", admission)
    return failures


def test_heartbeat_resumes_after_transient_busy(tmp_path, monkeypatch):
    import threading
    import time
    from src.services.graph_sync_queue import GraphSyncQueue
    contract = RuntimeContract(tmp_path / "data", "a" * 64)
    contract.initialize_empty()
    queue = GraphSyncQueue(None, contract.data_dir / "queue.sqlite3", auto_start=False,
                           runtime_contract=contract)
    queue.enqueue("remove_active", {"source_name": "synthetic.xlsx"})
    job = queue._claim_next()
    before = queue.status(job["id"])["updated_at"]
    _busy_once(monkeypatch, contract)
    queue.heartbeat_seconds = .02
    stop = threading.Event()
    thread = threading.Thread(target=queue._keep_alive, args=(job, stop))
    thread.start()
    try:
        deadline = time.monotonic() + 2
        while queue.status(job["id"])["updated_at"] == before and time.monotonic() < deadline:
            time.sleep(.01)
        assert queue.status(job["id"])["updated_at"] > before
    finally:
        stop.set()
        thread.join(2)


def test_async_queue_retries_transient_busy(tmp_path, monkeypatch):
    from src.services.graph_sync_queue import GraphSyncQueue
    contract = RuntimeContract(tmp_path / "data", "a" * 64)
    contract.initialize_empty()

    class Graph:
        def remove_active_name(self, name, **kwargs):
            return True

    queue = GraphSyncQueue(Graph(), contract.data_dir / "queue.sqlite3", auto_start=False,
                           runtime_contract=contract)
    job = queue.enqueue("remove_active", {"source_name": "synthetic.xlsx"})
    _busy_once(monkeypatch, contract)
    queue.start()
    assert queue.wait(job["id"], timeout=3)["status"] == "complete"


def test_refresh_retries_transient_busy(tmp_path, monkeypatch):
    from tests.test_graph_refresh_coordinator import SyntheticGraph, wait_finished
    from src.services.graph_sync_coordinator import GraphRefreshCoordinator
    contract = RuntimeContract(tmp_path / "data", "a" * 64)
    contract.initialize_empty()
    coordinator = GraphRefreshCoordinator(SyntheticGraph(), contract.data_dir / "active", runtime_contract=contract)
    failures = _busy_once(monkeypatch, contract, thread_name="graph-active-refresh")
    coordinator.request_refresh(force=True)
    assert wait_finished(coordinator)["outcome"] == "success"
    assert failures == ["busy"]


def test_watch_worker_defers_busy_cycle_instead_of_exiting(tmp_path, monkeypatch):
    import src.queue_worker as worker
    monkeypatch.setattr(worker, "APP_DATA_DIR", tmp_path / "data")
    contract = RuntimeContract(worker.APP_DATA_DIR, get_release_info()["source_sha256"])
    contract.initialize_empty()
    _busy_once(monkeypatch, contract)
    original_build = worker.build_queue
    monkeypatch.setattr(worker, "build_queue", lambda **kwargs: original_build(runtime_contract=contract))
    delays = []

    def sleep(seconds):
        delays.append(seconds)
        if len(delays) == 2:
            raise KeyboardInterrupt()
    monkeypatch.setattr(worker.time, "sleep", sleep)
    with pytest.raises(KeyboardInterrupt):
        worker.main(["--watch"])
    assert len(delays) == 2


def test_nested_admission_does_not_leak_on_exception_or_admit_other_release(tmp_path):
    from src.services.runtime_admission import admit_contract
    contract = RuntimeContract(tmp_path / "data", "a" * 64)
    contract.initialize_empty()
    duplicate = RuntimeContract(contract.data_dir, "a" * 64)
    wrong = RuntimeContract(contract.data_dir, "b" * 64)
    with pytest.raises(ValueError, match="synthetic"):
        with admit_contract(contract) as outer:
            with admit_contract(duplicate) as nested:
                assert outer is nested
                with pytest.raises(ContractError):
                    with admit_contract(wrong):
                        pytest.fail("Another release was admitted")
                raise ValueError("synthetic")
    contract.activate("b" * 64, next_formats=SUPPORTED_FORMATS)
    with pytest.raises(ContractError):
        with admit_contract(contract):
            pytest.fail("Old admission leaked")
    with admit_contract(wrong):
        pass
