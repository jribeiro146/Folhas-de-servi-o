"""Ownership fencing, heartbeat and OS exclusion over synthetic durable jobs."""
import multiprocessing
import threading
import time
from pathlib import Path

import pytest

from src.services.file_mutex import file_mutex
from src.services.graph_sync_queue import GraphSyncQueue, GraphJobNoLongerActiveError


def reclaimed(tmp_path):
    queue = GraphSyncQueue(None, tmp_path / "synthetic.sqlite3", auto_start=False)
    queued = queue.enqueue("remove_active", {"source_name": "synthetic.xlsx"})
    old = queue._claim_next()
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET updated_at=0 WHERE id=?", (queued["id"],))
    new = queue._claim_next()
    assert new["attempt_token"] != old["attempt_token"]
    queue._attempt_context.job = old
    return queue, old, new


@pytest.mark.parametrize("action", ["complete", "fail", "checkpoint", "send", "execute"])
def test_old_attempt_cannot_overwrite_new_attempt_or_send(tmp_path, action):
    queue, old, new = reclaimed(tmp_path)
    before = queue.status(new["id"])
    calls = []
    class Mail:
        def send_prepared(self, *args, **kwargs):
            calls.append("send")
    queue.mail_service = Mail()
    with pytest.raises(GraphJobNoLongerActiveError):
        if action == "complete":
            queue._mark_complete(old, {"old": True})
        elif action == "fail":
            queue._mark_failed(old, RuntimeError("old error"))
        elif action == "checkpoint":
            queue._checkpoint_result(old["id"], {"phase": "old"})
        elif action == "send":
            queue._send_mail_once({}, tmp_path / "unused.pdf", job_id=old["id"], prior_result={})
        else:
            queue._execute(old["kind"], old["payload"], job_id=old["id"])
    assert queue.status(new["id"]) == before
    assert calls == []
    assert not queue._heartbeat(old)


def test_old_failure_fallback_and_terminal_hold_preserve_new_attempt(tmp_path):
    queue, old, new = reclaimed(tmp_path)
    before = queue.status(new["id"])
    queue._record_job_failure(old, RuntimeError("old error"))
    queue._hold_job_terminally(old, "old hold")
    assert queue.status(new["id"]) == before
    assert queue._heartbeat(new)
    queue._mark_complete(new, {"new": True})
    assert queue.status(new["id"])["result"] == {"new": True}


def test_live_worker_excludes_second_worker_even_if_timestamp_looks_expired(tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls = []
    class Graph:
        def remove_active_name(self, name, **kwargs):
            calls.append(name)
            entered.set()
            assert release.wait(5)
            return True
    first = GraphSyncQueue(Graph(), tmp_path / "synthetic.sqlite3", auto_start=False)
    second = GraphSyncQueue(Graph(), first.database_path, auto_start=False)
    job = first.enqueue("remove_active", {"source_name": "synthetic.xlsx"})
    worker = threading.Thread(target=first.run_until_idle)
    worker.start()
    try:
        assert entered.wait(3)
        with first._connect() as db:
            db.execute("UPDATE graph_sync_jobs SET updated_at=0 WHERE id=?", (job["id"],))
        second.run_until_idle()
        assert second.status(job["id"])["status"] == "running"
        assert second.status(job["id"])["attempts"] == 1
        assert calls == ["synthetic.xlsx"]
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert first.status(job["id"])["status"] == "complete"


def test_heartbeat_keeps_long_running_attempt_current(tmp_path):
    queue = GraphSyncQueue(None, tmp_path / "synthetic.sqlite3", auto_start=False)
    queue.enqueue("remove_active", {"source_name": "synthetic.xlsx"})
    job = queue._claim_next()
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET updated_at=0 WHERE id=?", (job["id"],))
    queue.heartbeat_seconds = .02
    stop = threading.Event()
    heartbeat = threading.Thread(target=queue._keep_alive, args=(job, stop))
    heartbeat.start()
    try:
        deadline = time.monotonic() + 2
        while queue.status(job["id"])["updated_at"] == 0 and time.monotonic() < deadline:
            time.sleep(.01)
        assert queue.status(job["id"])["updated_at"] > time.time() - 2
    finally:
        stop.set()
        heartbeat.join(2)


def hold_lock_in_process(path, entered, release):
    with file_mutex(Path(path)):
        entered.set()
        release.wait(10)


@pytest.mark.parametrize("crash", [False, True])
def test_another_process_lock_prevents_claim_and_release_allows_recovery(tmp_path, crash):
    context = multiprocessing.get_context("spawn")
    entered, release = context.Event(), context.Event()
    calls = []
    class Graph:
        def remove_active_name(self, name, **kwargs):
            calls.append(name)
            return True
    queue = GraphSyncQueue(Graph(), tmp_path / "synthetic.sqlite3", auto_start=False)
    job = queue.enqueue("remove_active", {"source_name": "synthetic.xlsx"})
    child = context.Process(target=hold_lock_in_process, args=(str(queue._worker_mutex_path), entered, release))
    child.start()
    try:
        assert entered.wait(5)
        queue.run_until_idle()
        assert queue.status(job["id"])["status"] == "pending"
        assert calls == []
    finally:
        if crash:
            child.terminate()
        else:
            release.set()
        child.join(5)
        if child.is_alive():
            child.terminate()
            child.join(2)
    assert child.exitcode != 0 if crash else child.exitcode == 0
    queue.run_until_idle()
    assert queue.status(job["id"])["status"] == "complete"
    assert calls == ["synthetic.xlsx"]


def test_old_completion_cannot_delete_assets_needed_by_reclaimed_attempt(tmp_path):
    folder = tmp_path / "synthetic"
    folder.mkdir()
    file = folder / "synthetic.xlsx"
    file.write_bytes(b"synthetic")
    queue = GraphSyncQueue(None, tmp_path / "synthetic.sqlite3", auto_start=False)
    queued = queue.enqueue("upload_active", {"draft_path": str(file)})
    old = queue._claim_next()
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET updated_at=0 WHERE id=?", (queued["id"],))
    new = queue._claim_next()
    staged = Path(new["payload"]["draft_path"])
    with pytest.raises(GraphJobNoLongerActiveError):
        queue._mark_complete(old, {"old": True})
    assert staged.read_bytes() == b"synthetic"
    queue._mark_complete(new, {"new": True})
    assert not staged.exists()
