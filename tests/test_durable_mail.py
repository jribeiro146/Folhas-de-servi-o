"""Recover the exact prepared message across real process interruption."""
import base64
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from contextlib import closing
from dataclasses import replace
from urllib.error import HTTPError

import pytest

from src.services.graph_mail_service import GraphMailConfig, GraphMailService, GraphMailError
from src.services.graph_sync_queue import GraphSyncQueue


class Graph:
    def __init__(self, root, *, crash=False, resume=False):
        self.root = Path(root)
        self.crash = crash
        self.resume = resume

    def upload_archive_bundle(self, _):
        assert not self.resume, "An existing message must not upload the archive again"
        return ["synthetic.xlsx"]

    def export_archive_pdf(self, _):
        assert not self.resume, "An existing message must not generate the PDF again"
        pdf = self.root / "synthetic.pdf"
        pdf.write_bytes(b"%PDF-original")
        return pdf, "synthetic.pdf"

    def remove_active_name(self, *args, **kwargs):
        marker = self.root / "remote-removed"
        if marker.exists():
            return False
        with marker.open("wb") as stream:
            stream.write(b"one simulated remote deletion")
            stream.flush()
            os.fsync(stream.fileno())
        if self.crash:
            os._exit(23)
        return True


def make_mail(calls):
    def transport(req, **kwargs):
        calls.append(req)
        return io.BytesIO(b'{"access_token":"synthetic","expires_in":3600}')
    return GraphMailService(GraphMailConfig("synthetic", "synthetic", "synthetic", "sender@example.invalid"),
                            transport=transport)


def crash_worker(root):
    GraphSyncQueue(Graph(root, crash=True), Path(root) / "queue.sqlite3",
                   mail_service=make_mail([]), auto_start=False).run_until_idle()


@pytest.mark.parametrize("etag", ["synthetic-etag", None])
def test_crash_after_removal_recovers_fixed_mail_without_pdf_or_upload(tmp_path, etag):
    queue = GraphSyncQueue(Graph(tmp_path), tmp_path / "queue.sqlite3", mail_service=make_mail([]),
                          auto_start=False)
    job = queue.enqueue("archive_and_remove", {"archived_path": str(tmp_path / "synthetic.xlsx"),
        "source_name": "synthetic.xlsx", "expected_etag": etag,
        "mail": {"to": "customer@example.invalid", "subject": "Original"}}, job_id="fixed:crash")
    script = """
from tools.test_runtime import configure, block_outbound_network
configure()
block_outbound_network()
from tests.test_durable_mail import crash_worker
import sys
crash_worker(sys.argv[1])
"""
    process = subprocess.run([sys.executable, "-B", "-c", script, str(tmp_path)],
                             cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=30)
    assert process.returncode == 23, process.stderr.decode("utf-8", errors="replace")
    state = queue.status(job["id"])
    assert state["result"]["phase"] == "removing_active"
    assert state["result"]["mail_snapshot"]
    (tmp_path / "synthetic.pdf").unlink()
    with sqlite3.connect(queue.database_path) as db:
        db.execute("UPDATE graph_sync_jobs SET updated_at=0 WHERE id=?", (job["id"],))
    assert queue.status(job["id"])["status"] == "failed"
    assert not queue.status(job["id"])["reconciliation_required"]
    queue.retry(job["id"])
    calls = []
    graph = Graph(tmp_path, resume=True)
    if etag is None:
        graph.remove_active_name = lambda *a, **k: pytest.fail("Uncertain removal without eTag must stay held")
    resumed = GraphSyncQueue(graph, queue.database_path,
                            mail_service=make_mail(calls), auto_start=False)
    resumed.run_until_idle()
    state = resumed.status(job["id"])
    if etag is None:
        assert state["status"] == "failed" and state["result"]["invalid_state"]
        assert state["result"]["phase"] == "removal_unknown"
        assert not state["will_retry"] and calls == []
        return
    assert state["status"] == "complete"
    assert state["result"]["mail"]["accepted"] is True
    sends = [req for req in calls if req.full_url.endswith("/sendMail")]
    assert len(sends) == 1
    message = json.loads(sends[0].data)["message"]
    assert message["subject"] == "Original"
    assert base64.b64decode(message["attachments"][0]["contentBytes"]) == b"%PDF-original"


def ready_job(tmp_path):
    graph = Graph(tmp_path)
    def stop(*args, **kwargs):
        raise GraphMailError("Synthetic stop before remove")
    graph.remove_active_name = stop
    queue = GraphSyncQueue(graph, tmp_path / "queue.sqlite3", mail_service=make_mail([]), auto_start=False)
    job = queue.enqueue("archive_and_remove", {"archived_path": str(tmp_path / "synthetic.xlsx"),
        "source_name": "synthetic.xlsx", "mail": {"to": "customer@example.invalid"}})
    queue.run_until_idle()
    assert queue.status(job["id"])["result"]["mail_snapshot"]
    return queue, job


@pytest.mark.parametrize("damage", ["missing", "bytes", "hash", "identity", "retired", "oversize", "marker"])
def test_corrupt_message_stays_held_without_rebuilding_or_removing(tmp_path, damage):
    queue, job = ready_job(tmp_path)
    with closing(sqlite3.connect(queue.database_path)) as db:
        if damage == "missing":
            db.execute("DELETE FROM graph_mail_messages")
        elif damage == "bytes":
            db.execute("UPDATE graph_mail_messages SET payload=?", (b"x" * 20,))
        elif damage == "hash":
            db.execute("UPDATE graph_mail_messages SET sha256=?", ("f" * 64,))
        elif damage == "identity":
            db.execute("UPDATE graph_mail_messages SET sender='different@example.invalid'")
        elif damage == "retired":
            db.execute("UPDATE graph_mail_messages SET payload=NULL,retired_at=1")
        elif damage == "oversize":
            db.execute("UPDATE graph_mail_messages SET payload=zeroblob(4000001)")
        else:
            state = queue.status(job["id"])["result"]
            state.pop("mail_snapshot")
            db.execute("UPDATE graph_sync_jobs SET result_json=? WHERE id=?", (json.dumps(state), job["id"]))
        db.commit()
    queue.retry(job["id"])
    calls = []
    resumed = GraphSyncQueue(Graph(tmp_path, resume=True), queue.database_path,
                            mail_service=make_mail(calls), auto_start=False)
    resumed.run_until_idle()
    state = resumed.status(job["id"])
    assert state["status"] == "failed" and state["result"]["invalid_state"]
    assert state["will_retry"] is False
    with pytest.raises(ValueError, match="inválidos"):
        resumed.retry(job["id"])
    assert not (tmp_path / "remote-removed").exists()
    assert calls == []


def test_changed_test_identity_does_not_remove_or_rebuild_a_saved_message(tmp_path):
    queue, job = ready_job(tmp_path)
    queue.retry(job["id"])
    calls = []
    mail = make_mail(calls)
    mail.config = replace(mail.config, test_recipient="sandbox@example.invalid")
    resumed = GraphSyncQueue(Graph(tmp_path, resume=True), queue.database_path, mail_service=mail, auto_start=False)
    resumed.run_until_idle()
    state = resumed.status(job["id"])
    assert state["status"] == "failed" and not state["will_retry"]
    assert state["result"]["phase"] == "mail_rejected"
    assert state["result"]["mail_snapshot"]
    assert not (tmp_path / "remote-removed").exists()
    assert calls == []


def test_failed_snapshot_transaction_does_not_publish_marker_or_remove_active(tmp_path, monkeypatch):
    graph = Graph(tmp_path)
    queue = GraphSyncQueue(graph, tmp_path / "queue.sqlite3", mail_service=make_mail([]), auto_start=False)
    job = queue.enqueue("archive_and_remove", {"archived_path": str(tmp_path / "synthetic.xlsx"),
        "source_name": "synthetic.xlsx", "mail": {"to": "customer@example.invalid"}})
    original_connect = sqlite3.connect
    class FailedCommit(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.startswith("UPDATE graph_sync_jobs SET result_json=") and '"message_ready"' in str(parameters[0]):
                raise sqlite3.OperationalError("Synthetic failure after message INSERT")
            return super().execute(sql, parameters)
    def connect(*args, **kwargs):
        if args and Path(str(args[0])) == queue.database_path:
            kwargs["factory"] = FailedCommit
        return original_connect(*args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", connect)
    queue.run_until_idle()
    state = queue.status(job["id"])
    assert state["status"] == "failed" and not state["result"].get("mail_snapshot")
    with closing(original_connect(queue.database_path)) as db:
        assert db.execute("SELECT COUNT(*) FROM graph_mail_messages").fetchone()[0] == 0
    assert not (tmp_path / "remote-removed").exists()


def test_queue_v2_rejects_old_storage_and_old_validator_rejects_new_storage(tmp_path):
    from tests import legacy_sqlite_format_v1 as old
    from src.services.runtime_contract import ContractError
    old_path = tmp_path / "old.sqlite3"
    with closing(sqlite3.connect(old_path)) as db:
        old.initialize_store(db, "queue")
    with pytest.raises(ContractError):
        GraphSyncQueue(None, old_path, auto_start=False)
    queue = GraphSyncQueue(None, tmp_path / "new.sqlite3", auto_start=False)
    with closing(sqlite3.connect(queue.database_path)) as db:
        with pytest.raises(ContractError):
            old.validate_store(db, "queue")
    with closing(queue._connect()) as db:
        assert db.execute("PRAGMA synchronous").fetchone()[0] == 2


def test_known_rejection_reuses_bytes_skips_confirmed_removal_and_retires_on_acceptance(tmp_path):
    calls = []
    reject = [True]
    def transport(req, **kwargs):
        if req.full_url.endswith("/sendMail"):
            calls.append(req.data)
            if reject[0]:
                raise HTTPError(req.full_url, 400, "Synthetic refusal", {}, io.BytesIO(b'{}'))
        return io.BytesIO(b'{"access_token":"synthetic","expires_in":3600}')
    mail = GraphMailService(make_mail([]).config, transport=transport)
    queue = GraphSyncQueue(Graph(tmp_path), tmp_path / "queue.sqlite3", mail_service=mail, auto_start=False)
    job = queue.enqueue("archive_and_remove", {"archived_path": str(tmp_path / "synthetic.xlsx"),
        "source_name": "synthetic.xlsx", "mail": {"to": "customer@example.invalid"}})
    queue.run_until_idle()
    state = queue.status(job["id"])
    assert state["status"] == "failed" and state["result"]["active_removed"]
    with closing(sqlite3.connect(queue.database_path)) as db:
        before = db.execute("SELECT payload,sha256,retired_at FROM graph_mail_messages").fetchone()
    assert before[0] == calls[0] and before[2] is None
    (tmp_path / "synthetic.pdf").unlink()
    graph = Graph(tmp_path, resume=True)
    graph.remove_active_name = lambda *a, **k: pytest.fail("Do not remove a new namesake after confirmed removal")
    reject[0] = False
    queue.retry(job["id"])
    resumed = GraphSyncQueue(graph, queue.database_path, mail_service=mail, auto_start=False)
    resumed.run_until_idle()
    state = resumed.status(job["id"])
    assert state["status"] == "complete" and state["result"]["phase"] == "mail_accepted"
    assert calls == [before[0], before[0]]
    with closing(sqlite3.connect(queue.database_path)) as db:
        after = db.execute("SELECT payload,sha256,retired_at FROM graph_mail_messages").fetchone()
    assert after[0] is None and after[1] == before[1] and after[2] > 0


def test_receipt_and_retirement_roll_back_together_without_replaying_accepted_send(tmp_path, monkeypatch):
    calls = []
    queue = GraphSyncQueue(Graph(tmp_path), tmp_path / "queue.sqlite3", mail_service=make_mail(calls), auto_start=False)
    job = queue.enqueue("archive_and_remove", {"archived_path": str(tmp_path / "synthetic.xlsx"),
        "source_name": "synthetic.xlsx", "mail": {"to": "customer@example.invalid"}})
    original_connect = sqlite3.connect
    class RetirementFails(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.startswith("UPDATE graph_mail_messages SET payload=NULL"):
                raise sqlite3.OperationalError("Synthetic failure after receipt UPDATE")
            return super().execute(sql, parameters)
    def connect(*args, **kwargs):
        if args and Path(str(args[0])) == queue.database_path:
            kwargs["factory"] = RetirementFails
        return original_connect(*args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", connect)
    queue.run_until_idle()
    state = queue.status(job["id"])
    assert state["status"] == "failed" and state["reconciliation_required"]
    assert state["result"]["phase"] == "sending_mail" and not state["will_retry"]
    with closing(original_connect(queue.database_path)) as db:
        stored = db.execute("SELECT payload,retired_at FROM graph_mail_messages").fetchone()
    sends = [req for req in calls if req.full_url.endswith("/sendMail")]
    assert len(sends) == 1 and stored[0] == sends[0].data and stored[1] is None
    with pytest.raises(ValueError, match="reconciliação"):
        queue.retry(job["id"])
    queue.run_until_idle()
    assert len([req for req in calls if req.full_url.endswith("/sendMail")]) == 1
