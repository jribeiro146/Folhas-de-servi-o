"""Independent diagnostics over synthetic directories; no operational imports in children."""
import json
import multiprocessing
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from src.services import worker_health as health_module
from src.services.worker_health import WorkerHealth, read_health, safe_directory, storage_id
from src.services.file_mutex import FileMutexBusy, file_mutex

RELEASE = {"revision": "a" * 40, "source_sha256": "b" * 64, "verified": True}
IDENTITY = "c" * 64


def read(directory, **kwargs):
    return read_health(directory, IDENTITY, expected_sha256=RELEASE["source_sha256"], **kwargs)


def test_no_channel_is_not_proof_worker_never_started(tmp_path):
    assert read(None)["state"] == "not_configured"
    assert read(tmp_path)["state"] == "never_seen"
    assert not list(tmp_path.iterdir())


def test_lifecycle_version_age_and_result_are_separate(tmp_path):
    with WorkerHealth(tmp_path, IDENTITY) as reporter:
        assert read(tmp_path)["state"] == "starting"
        reporter.update("cycling", release=RELEASE)
        state = read(tmp_path)
        assert state["state"] == "cycling" and state["cycle_started_at"]
        assert state["last_result_code"] is None
        reporter.update("idle", code="cycle_completed")
        state = read(tmp_path)
        assert state["state"] == "idle" and state["cycle_completed_at"]
        assert state["last_result_code"] == "cycle_completed"
        assert read(tmp_path, now=state["heartbeat_at"] + 36)["state"] == "stale"
        assert read(tmp_path, now=state["heartbeat_at"] - 10)["state"] == "clock_invalid"
        assert read_health(tmp_path, IDENTITY, expected_sha256="d" * 64)["state"] == "version_mismatch"
        assert read_health(tmp_path, "e" * 64)["state"] == "wrong_storage"
        reporter.update("stopped")
        assert read(tmp_path)["state"] == "stopped"
        assert read(tmp_path)["last_result_code"] == "cycle_completed"
    assert not reporter.thread.is_alive()
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("phase", ["waiting", "refused", "failed", "stopped"])
def test_failure_states_keep_only_allowlisted_code(tmp_path, phase):
    with WorkerHealth(tmp_path, IDENTITY) as reporter:
        reporter.update(phase, code="contract_refused")
        result = read(tmp_path)
        assert result["state"] == phase and result["last_result_code"] == "contract_refused"


@pytest.mark.parametrize("change", [
    {"phase": ["private"]}, {"last_result_code": "private@example.invalid"},
    {"heartbeat_at": float("nan")}, {"started_at": True}, {"format": True},
    {"release": {"source_sha256": "b" * 64, "revision": "private/path", "verified": True}},
])
def test_malformed_record_is_safe_and_never_healthy(tmp_path, change):
    with WorkerHealth(tmp_path, IDENTITY) as reporter:
        data = dict(reporter.data)
    data.update(change)
    (tmp_path / "worker.json").write_text(json.dumps(data), encoding="utf-8")
    assert read(tmp_path) == {"state": "unavailable"}


def test_bounded_read_discards_unknown_fields_and_paths(tmp_path):
    with WorkerHealth(tmp_path, IDENTITY) as reporter:
        reporter.update("idle", release=RELEASE)
        data = dict(reporter.data)
    data["private"] = "/private/synthetic/customer.xlsx"
    data["release"]["private"] = "synthetic-token"
    (tmp_path / "worker.json").write_text(json.dumps(data), encoding="utf-8")
    result = read(tmp_path)
    assert result["state"] == "idle"
    assert "private" not in json.dumps(result) and "storage_id" not in result
    (tmp_path / "worker.json").write_text("x" * 4097, encoding="utf-8")
    assert read(tmp_path)["state"] == "unavailable"


@pytest.mark.parametrize("directory", ["relative", "//server/share/health"])
def test_relative_or_network_directory_is_refused(directory):
    with pytest.raises(ValueError):
        safe_directory(directory)


def test_channel_cannot_contaminate_operational_data_or_code(tmp_path, monkeypatch):
    from src import config
    for path in (config.APP_DATA_DIR / "health", config.GRAPH_CACHE_DIR / "health",
                 config.EXCEL_ACTIVAS_DIR / "health", tmp_path / "src" / "health", tmp_path / "tools"):
        monkeypatch.setattr(config, "WORKER_HEALTH_DIR", str(path))
        with pytest.raises(ValueError):
            health_module.configured_channel(release_root=tmp_path)
        assert not path.exists()


def hold_diagnostics(directory, entered, release):
    with WorkerHealth(Path(directory), IDENTITY) as reporter:
        reporter.update("cycling", release=RELEASE)
        entered.set()
        release.wait(30)


def test_crashed_writer_becomes_stale_and_os_lock_allows_replacement(tmp_path):
    context = multiprocessing.get_context("spawn")
    entered, release = context.Event(), context.Event()
    process = context.Process(target=hold_diagnostics, args=(str(tmp_path), entered, release))
    process.start()
    try:
        assert entered.wait(10)
        before = (tmp_path / "worker.json").read_bytes()
        with pytest.raises(FileMutexBusy):
            with WorkerHealth(tmp_path, "f" * 64):
                pytest.fail("Second writer replaced a live worker")
        assert (tmp_path / "worker.json").read_bytes() == before
    finally:
        process.terminate()
        process.join(5)
    stamp = read(tmp_path)["heartbeat_at"]
    assert read(tmp_path, now=stamp + 36)["state"] == "stale"
    with WorkerHealth(tmp_path, IDENTITY):
        assert read(tmp_path)["state"] == "starting"


def test_heartbeat_continues_in_long_cycle_without_claiming_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(health_module, "HEARTBEAT_SECONDS", .02)
    with WorkerHealth(tmp_path, IDENTITY) as reporter:
        reporter.update("cycling", release=RELEASE)
        first = read(tmp_path)
        deadline = time.monotonic() + 2
        while read(tmp_path)["heartbeat_at"] <= first["heartbeat_at"] and time.monotonic() < deadline:
            threading.Event().wait(.01)
        last = read(tmp_path)
        assert last["heartbeat_at"] > first["heartbeat_at"]
        assert last["cycle_started_at"] == first["cycle_started_at"]
        assert last["cycle_completed_at"] is None and last["last_result_code"] is None


@pytest.fixture
def worker_channel(tmp_path, monkeypatch):
    from src import config, queue_worker as worker
    data, channel = tmp_path / "data", tmp_path / "health"
    monkeypatch.setattr(config, "APP_DATA_DIR", data)
    monkeypatch.setattr(worker, "APP_DATA_DIR", data)
    monkeypatch.setattr(config, "WORKER_HEALTH_DIR", str(channel))
    return worker, data, channel


def test_unprepared_start_records_refusal_without_creating_data(worker_channel):
    worker, data, channel = worker_channel
    assert worker.main([]) == 1
    assert not data.exists()
    result = read_health(channel, storage_id(data))
    assert result["state"] == "refused" and result["last_result_code"] == "contract_refused"
    assert result["release"]["source_sha256"]


@pytest.mark.parametrize("failure", ["identity", "contract", "storage"])
def test_real_admission_failures_preserve_all_rejected_files(worker_channel, tmp_path, failure):
    from src.services.runtime_contract import RuntimeContract
    from src.services.release_identity import get_release_info, write_release_manifest
    worker, data, channel = worker_channel
    release_root = None
    if failure == "identity":
        release_root = tmp_path / "release"
        (release_root / "src").mkdir(parents=True)
        source = release_root / "src" / "synthetic.py"
        source.write_text("synthetic = 1", encoding="utf-8")
        write_release_manifest(release_root, revision="a" * 40)
        source.write_text("synthetic = 2", encoding="utf-8")
    else:
        RuntimeContract(data, "a" * 64 if failure == "contract" else get_release_info()["source_sha256"]).initialize_empty()
        if failure == "storage":
            (data / "graph-sync.sqlite3").write_bytes(b"synthetic invalid store")
    before = {str(path): path.read_bytes() for path in data.rglob("*") if path.is_file()}
    assert worker.main([], release_root=release_root) == 1
    assert {str(path): path.read_bytes() for path in data.rglob("*") if path.is_file()} == before
    result = read_health(channel, storage_id(data))
    assert result["state"] == "refused" and result["last_result_code"] == failure + "_refused"
    assert result["release"] is None if failure == "identity" else result["release"]["source_sha256"]


def test_worker_cycles_and_clean_exit_are_observable(worker_channel, monkeypatch):
    from src.services.runtime_contract import RuntimeContract
    from src.services.release_identity import get_release_info
    worker, data, channel = worker_channel
    RuntimeContract(data, get_release_info()["source_sha256"]).initialize_empty()
    observed = []
    def sleep(_seconds):
        observed.append(read_health(channel, storage_id(data)))
        raise KeyboardInterrupt()
    monkeypatch.setattr(worker.time, "sleep", sleep)
    with pytest.raises(KeyboardInterrupt):
        worker.main(["--watch"])
    assert observed[0]["state"] == "idle" and observed[0]["last_result_code"] == "cycle_completed"
    assert read_health(channel, storage_id(data))["state"] == "stopped"
    assert worker.main([]) == 0
    assert read_health(channel, storage_id(data))["last_result_code"] == "cycle_completed"


def test_transient_busy_is_reported_during_wait_then_recovers(worker_channel, monkeypatch):
    from src.services.runtime_contract import RuntimeContract, ContractBusy
    from src.services.release_identity import get_release_info
    worker, data, channel = worker_channel
    RuntimeContract(data, get_release_info()["source_sha256"]).initialize_empty()
    original = worker.build_queue
    calls = []
    def build(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise ContractBusy("synthetic private details")
        return original(**kwargs)
    observations = []
    def sleep(_seconds):
        observations.append(read_health(channel, storage_id(data)))
        if len(observations) == 2:
            raise KeyboardInterrupt()
    monkeypatch.setattr(worker, "build_queue", build)
    monkeypatch.setattr(worker.time, "sleep", sleep)
    with pytest.raises(KeyboardInterrupt):
        worker.main(["--watch"])
    assert observations[0]["state"] == "waiting" and observations[0]["last_result_code"] == "contract_busy"
    assert observations[1]["state"] == "idle"


def test_queue_mutex_contention_is_not_reported_as_success(tmp_path):
    from src.services.graph_sync_queue import GraphSyncQueue
    queue = GraphSyncQueue(None, tmp_path / "queue.sqlite3", auto_start=False)
    with file_mutex(queue._worker_mutex_path):
        assert queue.run_until_idle() is False
    assert queue.run_until_idle() is True


def test_diagnostic_write_failure_does_not_stop_processing(worker_channel, monkeypatch, caplog):
    from src.services.runtime_contract import RuntimeContract
    from src.services.release_identity import get_release_info
    worker, data, channel = worker_channel
    RuntimeContract(data, get_release_info()["source_sha256"]).initialize_empty()
    original = Path.replace
    def replace(path, target):
        if Path(target) == channel / "worker.json":
            raise OSError("synthetic disk full private-marker")
        return original(path, target)
    monkeypatch.setattr(Path, "replace", replace)
    assert worker.main([]) == 0
    assert "worker_health_unavailable" in caplog.text and "private-marker" not in caplog.text
    assert read_health(channel, storage_id(data))["state"] == "never_seen"
    assert not list(channel.glob("*.tmp"))


@pytest.mark.parametrize("interval", ["nan", "inf", "-1", "0", "3601"])
def test_invalid_interval_refuses_without_starting_worker(worker_channel, interval):
    worker, data, channel = worker_channel
    with pytest.raises(SystemExit) as caught:
        worker.main(["--interval", interval])
    assert caught.value.code == 2 and not channel.exists() and not data.exists()


def test_offline_cli_reads_without_importing_app_or_env(tmp_path):
    channel, data = tmp_path / "health", tmp_path / "unprepared"
    with WorkerHealth(channel, storage_id(data)) as reporter:
        reporter.update("idle", release=RELEASE)
        result = subprocess.run([sys.executable, "-B", "tools/worker_health.py",
            "--health-dir", str(channel), "--data-dir", str(data), "--expected-sha256", "b" * 64],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=15)
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        assert json.loads(result.stdout)["state"] == "idle"
    assert not data.exists()


def test_backlog_includes_held_failed_and_teams_but_not_complete(tmp_path):
    from src.services.graph_sync_queue import GraphSyncQueue
    queue = GraphSyncQueue(None, tmp_path / "queue.sqlite3", auto_start=False)
    for kind, status, age in [("remove_active", "held", 600), ("notify_teams", "failed", 300),
                               ("remove_active", "complete", 3600)]:
        job = queue.enqueue(kind, {})
        with queue._connect() as db:
            db.execute("UPDATE graph_sync_jobs SET status=?,created_at=? WHERE id=?",
                       (status, time.time() - age, job["id"]))
    result = queue.diagnostic_backlog()
    assert 600 <= result["oldest_unfinished_age_seconds"] < 605
    assert result["teams_unfinished"] == 1


@pytest.mark.parametrize("scenario,expected,exit_code", [
    ("idle", "idle", 0), ("cycling", "cycling", 0),
    ("host_path", "wrong_storage", 1), ("stale", "stale", 1),
    ("other_version", "version_mismatch", 1),
])
def test_cli_uses_worker_path_identity_and_refuses_stale_or_other_version(tmp_path, scenario, expected, exit_code):
    # Only a path identity is calculated; /app/data is never opened/created.
    # This is a local CLI test, not an assertion that Docker/Linux was run.
    channel = tmp_path / "health"
    with WorkerHealth(channel, storage_id(Path("/app/data"))) as reporter:
        reporter.update("cycling" if scenario == "cycling" else "idle", release=RELEASE)
    if scenario == "stale":
        record = json.loads((channel / "worker.json").read_text(encoding="utf-8"))
        record["heartbeat_at"] = time.time() - 100
        (channel / "worker.json").write_text(json.dumps(record), encoding="utf-8")
    actual_data = str(tmp_path / "host-volume") if scenario == "host_path" else "/app/data"
    result = subprocess.run([sys.executable, "-B", "tools/worker_health.py",
        "--health-dir", str(channel), "--data-dir", actual_data,
        "--expected-sha256", ("d" if scenario == "other_version" else "b") * 64],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=15)
    assert result.returncode == exit_code, result.stderr.decode(errors="replace")
    assert json.loads(result.stdout)["state"] == expected
    assert not (tmp_path / "host-volume").exists()


def test_admin_reads_health_without_exposing_it_to_technicians(tmp_path, monkeypatch):
    from src import config
    from tests.test_diagnostic_privacy import diagnostic_app, assert_private
    from tests.test_graph_job_visibility import ADMIN_ID, login
    from src.services import admin_access
    app, _, _, _, queue, _ = diagnostic_app.__wrapped__(tmp_path, monkeypatch)
    channel = tmp_path / "health"
    monkeypatch.setattr(config, "WORKER_HEALTH_DIR", str(channel))
    with WorkerHealth(channel, storage_id(config.APP_DATA_DIR)) as reporter:
        reporter.update("idle", code="cycle_completed", release=app.config["RELEASE_INFO"])
        response = login(app, oid=ADMIN_ID, email=admin_access.ADMIN_EMAIL).get("/api/admin/overview")
        assert response.status_code == 200 and "no-store" in response.headers["Cache-Control"]
        assert response.json["worker"]["state"] == "idle"
        assert response.json["queue_backlog"]["teams_unfinished"] == 0
        assert str(channel) not in response.get_data(as_text=True)
        assert_private(response.get_data(as_text=True))
        technician = login(app)
        assert technician.get("/api/admin/overview").status_code == 403
        assert "worker" not in technician.get("/api/graph/status").json
