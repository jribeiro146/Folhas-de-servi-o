"""Never replay a Teams POST whose acceptance is uncertain."""
from contextlib import closing
from http.client import HTTPException, RemoteDisconnected
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import traceback
from urllib.error import HTTPError, URLError

import pytest

from src.services.graph_sync_queue import GraphSyncQueue
from src.services.teams_notification_service import TeamsNotificationConfig, TeamsNotificationService


class Response:
    status = 202
    headers = {}
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self, *_):
        return b""


def service(transport):
    return TeamsNotificationService(TeamsNotificationConfig("https://workflow.example.invalid/?sig=synthetic-private"),
                                    transport=transport)


def queued(tmp_path, transport, *, notification=None):
    teams = service(transport)
    queue = GraphSyncQueue(None, tmp_path / "queue.sqlite3", teams_notification_service=teams, auto_start=False)
    payload = notification if notification is not None else teams.prepare_service_sent_notification(service_number="SYNTHETIC")
    job = queue.enqueue("notify_teams", {"notification": payload})
    return queue, job


@pytest.mark.parametrize("failure", ["url", "timeout", "reset", "disconnected", "http_protocol", "os", "unexpected", 408, 425, 500, 503])
def test_ambiguous_post_stays_held_across_restart_and_manual_retry(tmp_path, failure):
    calls = []
    def transport(req, **kwargs):
        calls.append(req.data)
        if isinstance(failure, int):
            raise HTTPError(req.full_url, failure, "synthetic-private", {}, io.BytesIO(b"synthetic-private"))
        raise {"url": URLError("synthetic-private"), "timeout": TimeoutError("synthetic-private"),
            "reset": ConnectionResetError("synthetic-private"), "disconnected": RemoteDisconnected("synthetic-private"),
            "http_protocol": HTTPException("synthetic-private"), "os": OSError("synthetic-private"),
            "unexpected": RuntimeError("synthetic-private")}[failure]
    queue, job = queued(tmp_path, transport)
    queue.run_until_idle()
    state = queue.status(job["id"])
    assert state["status"] == "failed" and state["reconciliation_required"] and not state["will_retry"]
    assert state["result"]["phase"] == "teams_unknown"
    assert "synthetic-private" not in state["last_error"]
    with pytest.raises(ValueError, match="reconciliação"):
        queue.retry(job["id"])
    restarted = GraphSyncQueue(None, queue.database_path, teams_notification_service=service(transport), auto_start=False)
    restarted.run_until_idle()
    assert len(calls) == 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 413, 422, 429])
def test_explicit_refusal_without_retry_after_is_terminal_and_not_ambiguous(tmp_path, status):
    calls = []
    def transport(req, **kwargs):
        calls.append(req)
        raise HTTPError(req.full_url, status, "private-error", {}, io.BytesIO(b"private-body"))
    queue, job = queued(tmp_path, transport)
    queue.run_until_idle()
    state = queue.status(job["id"])
    assert state["status"] == "failed" and not state["will_retry"] and not state["reconciliation_required"]
    assert state["result"]["phase"] == "teams_rejected"
    assert "private" not in state["last_error"]
    queue.run_until_idle()
    assert len(calls) == 1


def test_explicit_throttle_respects_delay_and_persisted_attempt_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("FS_TEAMS_JOB_MAX_ATTEMPTS", "2")
    calls = []
    def transport(req, **kwargs):
        calls.append(req.data)
        raise HTTPError(req.full_url, 429, "throttled", {"Retry-After": "700"}, io.BytesIO(b""))
    queue, job = queued(tmp_path, transport)
    queue.run_until_idle()
    state = queue.status(job["id"])
    assert state["will_retry"] and state["max_attempts"] == 2
    assert state["next_attempt_at"] >= time.time() + 695
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET next_attempt_at=0 WHERE id=?", (job["id"],))
    resumed = GraphSyncQueue(None, queue.database_path, teams_notification_service=service(transport), auto_start=False)
    resumed.run_until_idle()
    state = resumed.status(job["id"])
    assert state["attempts"] == 2 and not state["will_retry"] and not state["reconciliation_required"]
    assert len(calls) == 2 and calls[0] == calls[1]


def test_invalid_payload_is_known_before_post(tmp_path):
    queue, job = queued(tmp_path, lambda *a, **kw: pytest.fail("No POST"), notification={"payload": {"type": "wrong"}})
    queue.run_until_idle()
    state = queue.status(job["id"])
    assert state["status"] == "failed" and not state["will_retry"] and not state["reconciliation_required"]
    assert state["result"]["phase"] == "teams_rejected"


def crash_post_worker(root):
    def accepted_then_crash(req, **kwargs):
        with (Path(root) / "teams-accepted").open("wb") as stream:
            stream.write(req.data)
            stream.flush()
            os.fsync(stream.fileno())
        os._exit(41)
    GraphSyncQueue(None, Path(root) / "queue.sqlite3", teams_notification_service=service(accepted_then_crash),
                   auto_start=False).run_until_idle()


def test_crash_during_teams_post_is_retained_after_stale_deadline(tmp_path):
    queue, job = queued(tmp_path, lambda *a, **kw: pytest.fail("Only child process sends"))
    script = """
from tools.test_runtime import configure, block_outbound_network
configure()
block_outbound_network()
from tests.test_teams_uncertainty import crash_post_worker
import sys
crash_post_worker(sys.argv[1])
"""
    result = subprocess.run([sys.executable, "-B", "-c", script, str(tmp_path)],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=30)
    assert result.returncode == 41, result.stderr.decode(errors="replace")
    assert (tmp_path / "teams-accepted").is_file()
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET updated_at=0 WHERE id=?", (job["id"],))
    state = queue.status(job["id"])
    assert state["status"] == "failed" and state["reconciliation_required"]
    assert state["result"]["phase"] == "teams_unknown"
    queue.run_until_idle()
    assert queue.status(job["id"])["attempts"] == 1
    with pytest.raises(ValueError, match="reconciliação"):
        queue.retry(job["id"])


def test_accepted_teams_receipt_and_completion_commit_together(tmp_path, monkeypatch):
    calls = []
    queue, job = queued(tmp_path, lambda req, **kw: calls.append(req) or Response())
    original_connect = sqlite3.connect
    class FailedCompletion(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if "SET status = 'complete', result_json" in sql:
                raise sqlite3.OperationalError("Synthetic failure before receipt/completion commit")
            return super().execute(sql, parameters)
    def connect(*args, **kwargs):
        if args and Path(str(args[0])) == queue.database_path:
            kwargs["factory"] = FailedCompletion
        return original_connect(*args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", connect)
    queue.run_until_idle()
    state = queue.status(job["id"])
    assert state["status"] == "failed" and state["reconciliation_required"] and not state["will_retry"]
    assert state["result"]["phase"] == "sending_teams" and not state["result"].get("accepted")
    assert len(calls) == 1


def test_old_queue2_and_new_queue3_refuse_each_others_storage(tmp_path):
    from tests import legacy_sqlite_format_v2 as old
    from src.services.runtime_contract import ContractError
    old_path = tmp_path / "old.sqlite3"
    with closing(sqlite3.connect(old_path)) as db:
        old.initialize_store(db, "queue")
    with pytest.raises(ContractError):
        GraphSyncQueue(None, old_path, auto_start=False)
    current = GraphSyncQueue(None, tmp_path / "current.sqlite3", auto_start=False)
    with closing(sqlite3.connect(current.database_path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        with pytest.raises(ContractError):
            old.validate_store(db, "queue")


def test_accepted_receipt_survives_restart_without_service_or_repost(tmp_path):
    calls = []
    queue, job = queued(tmp_path, lambda req, **kw: calls.append(req.data) or Response())
    queue.run_until_idle()
    result = queue.status(job["id"])
    assert result["status"] == "complete" and result["result"]["accepted"] is True
    assert result["result"]["phase"] == "teams_accepted"
    restarted = GraphSyncQueue(None, queue.database_path, auto_start=False)
    restarted.run_until_idle()
    assert restarted.status(job["id"])["result"] == result["result"] and len(calls) == 1
    with pytest.raises(ValueError):
        restarted.retry(job["id"])


@pytest.mark.parametrize("value", ["", "-1", "tomorrow", "NaN", "999999999999999999", "86401"])
def test_invalid_or_excessive_retry_after_is_terminal(tmp_path, value):
    def transport(req, **kwargs):
        raise HTTPError(req.full_url, 429, "synthetic-private", {"Retry-After": value}, io.BytesIO(b""))
    queue, job = queued(tmp_path, transport)
    queue.run_until_idle()
    assert not queue.status(job["id"])["will_retry"]


def test_http_date_throttle_does_not_retry_before_server_date(tmp_path):
    from email.utils import formatdate
    deadline = int(time.time()) + 900
    def transport(req, **kwargs):
        raise HTTPError(req.full_url, 429, "throttled", {"Retry-After": formatdate(deadline, usegmt=True)}, io.BytesIO(b""))
    queue, job = queued(tmp_path, transport)
    queue.run_until_idle()
    state = queue.status(job["id"])
    assert state["will_retry"] and state["next_attempt_at"] >= deadline


@pytest.mark.parametrize("status,uncertain", [(302, True), (503, True), (403, False), (429, False)])
def test_transport_returning_http_response_has_same_classification(tmp_path, status, uncertain):
    response = Response()
    response.status = status
    queue, job = queued(tmp_path, lambda *args, **kwargs: response)
    queue.run_until_idle()
    state = queue.status(job["id"])
    assert state["status"] == "failed" and not state["will_retry"]
    assert state["reconciliation_required"] == uncertain


@pytest.mark.parametrize("status", [403, 503])
def test_error_trace_does_not_include_signed_url_or_response_body(status):
    from src.services.teams_notification_service import TeamsNotificationError
    def transport(req, **kwargs):
        raise HTTPError(req.full_url, status, "synthetic-private", {}, io.BytesIO(b"synthetic-private"))
    teams = service(transport)
    with pytest.raises(TeamsNotificationError) as error:
        teams.send_prepared(teams.prepare_service_sent_notification(service_number="SYNTHETIC"), operation_id="synthetic")
    assert "synthetic-private" not in "".join(traceback.format_exception(error.value))


def test_malformed_acceptance_is_quarantined_without_post(tmp_path):
    queue, job = queued(tmp_path, lambda *a, **kw: pytest.fail("No POST"))
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET result_json=? WHERE id=?",
                   (json.dumps({"accepted": "true", "phase": "teams_accepted"}), job["id"]))
    queue.run_until_idle()
    assert queue.status(job["id"])["result"]["invalid_state"]
