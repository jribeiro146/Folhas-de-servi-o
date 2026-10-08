"""A Teams intent commits with mail acceptance, using synthetic effects only."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from src.services.graph_sync_queue import GraphSyncQueue
from tests.fixed_mail_fake import FixedMailFake
from tests.test_durable_mail import Graph


NOTICE = {"service_number": "SYNTHETIC", "payload": {"type": "message", "attachments": [{}]}}


class AcceptedMail(FixedMailFake):
    def __init__(self, root):
        super().__init__()
        self.root = Path(root)

    def send_delivery(self, delivery):
        self.validate_delivery(delivery)
        with (self.root / "mail-accepted").open("ab") as stream:
            stream.write(b"one simulated acceptance\n")
            stream.flush()
            os.fsync(stream.fileno())
        return {"accepted": True}


def parent_job(queue, root):
    return queue.enqueue("archive_and_remove", {"archived_path": str(root / "synthetic.xlsx"),
        "source_name": "synthetic.xlsx", "expected_etag": "synthetic",
        "mail": {"to": "customer@example.invalid"}, "teams": NOTICE}, job_id="send:synthetic")


def child_rows(queue):
    with closing(sqlite3.connect(queue.database_path)) as db:
        return db.execute("SELECT id,kind,status,payload_json FROM graph_sync_jobs WHERE id LIKE 'teams:%'").fetchall()


def crash_worker(root, timing):
    root = Path(root)
    queue = GraphSyncQueue(Graph(root), root / "queue.sqlite3", mail_service=AcceptedMail(root), auto_start=False)
    if timing == "after":
        checkpoint = queue._checkpoint_result
        def stop_after(job_id, result):
            checkpoint(job_id, result)
            if result.get("phase") == "mail_accepted":
                os._exit(31)
        queue._checkpoint_result = stop_after
    else:
        original_connect = sqlite3.connect
        class StopBeforeCommit(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                value = super().execute(sql, parameters)
                if sql.startswith("UPDATE graph_mail_messages SET payload=NULL"):
                    os._exit(32)
                return value
        def connect(*args, **kwargs):
            if args and Path(str(args[0])) == queue.database_path:
                kwargs["factory"] = StopBeforeCommit
            return original_connect(*args, **kwargs)
        sqlite3.connect = connect
    queue.run_until_idle()


@pytest.mark.parametrize("timing", ["before", "after"])
def test_crash_around_receipt_commit_preserves_exactly_one_intent_without_resending(tmp_path, timing):
    queue = GraphSyncQueue(Graph(tmp_path), tmp_path / "queue.sqlite3", auto_start=False)
    parent = parent_job(queue, tmp_path)
    script = """
from tools.test_runtime import configure, block_outbound_network
configure()
block_outbound_network()
from tests.test_durable_teams import crash_worker
import sys
crash_worker(sys.argv[1], sys.argv[2])
"""
    process = subprocess.run([sys.executable, "-B", "-c", script, str(tmp_path), timing],
                             cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=30)
    assert process.returncode == (31 if timing == "after" else 32), process.stderr.decode(errors="replace")
    assert (tmp_path / "mail-accepted").read_text().splitlines() == ["one simulated acceptance"]
    children = child_rows(queue)
    assert len(children) == (1 if timing == "after" else 0)
    with closing(sqlite3.connect(queue.database_path)) as db:
        db.execute("UPDATE graph_sync_jobs SET updated_at=0 WHERE id=?", (parent["id"],))
        db.commit()
    resumed = GraphSyncQueue(Graph(tmp_path, resume=True), queue.database_path,
                            mail_service=AcceptedMail(tmp_path), auto_start=False)
    state = resumed.status(parent["id"])
    if timing == "after":
        assert state["status"] == "complete" and state["result"]["mail"]["accepted"]
        assert state["result"]["teams_job"]["id"] == "teams:" + parent["id"]
        resumed.run_until_idle()
        child = resumed.status("teams:" + parent["id"])
        assert child["status"] == "failed" and not child["will_retry"]  # service disabled, intent retained
        assert child["payload"] == {"notification": NOTICE}
        assert len(child_rows(resumed)) == 1
    else:
        assert state["status"] == "failed" and state["reconciliation_required"]
        with pytest.raises(ValueError, match="reconciliação"):
            resumed.retry(parent["id"])
        resumed.run_until_idle()
        assert child_rows(resumed) == []
    assert (tmp_path / "mail-accepted").read_text().splitlines() == ["one simulated acceptance"]


def test_stale_accepted_parent_recovers_missing_child_without_service_or_mail_replay(tmp_path):
    queue = GraphSyncQueue(None, tmp_path / "queue.sqlite3", auto_start=False)
    parent = parent_job(queue, tmp_path)
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET status='running',updated_at=0,result_json=? WHERE id=?",
                   (json.dumps({"phase": "mail_accepted", "mail": {"accepted": True}}), parent["id"]))
    for _ in range(3):
        assert queue.status(parent["id"])["status"] == "complete"
    children = child_rows(queue)
    assert len(children) == 1 and json.loads(children[0][3]) == {"notification": NOTICE}


@pytest.mark.parametrize("child_status", ["pending", "complete"])
def test_conflicting_child_is_preserved_and_does_not_invalidate_parent_receipt(tmp_path, child_status):
    class Teams:
        def send_prepared(self, *args, **kwargs):
            pytest.fail("Divergent intent must not be delivered")
    queue = GraphSyncQueue(Graph(tmp_path), tmp_path / "queue.sqlite3", mail_service=AcceptedMail(tmp_path),
                          teams_notification_service=Teams(), auto_start=False)
    parent = parent_job(queue, tmp_path)
    other = {"notification": {"service_number": "OTHER", "payload": {"type": "message", "attachments": [{}]}}}
    child = queue.enqueue("notify_teams", other, job_id="teams:" + parent["id"])
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET status=? WHERE id=?", (child_status, child["id"]))
    queue.run_until_idle()
    state = queue.status(parent["id"])
    assert state["status"] == "complete" and state["result"]["mail"]["accepted"]
    assert state["result"]["phase"] == "mail_accepted" and state["result"]["teams_child_conflict"]
    assert not state["result"].get("invalid_state")
    saved = queue.status(child["id"])
    assert saved["payload"] == other and not saved["will_retry"]
    assert saved["status"] == ("complete" if child_status == "complete" else "failed")
    if child_status == "pending":
        assert saved["result"]["previous_result_json"] is None  # preserve once, do not recursively nest diagnostics
    assert queue.summary()["teams_conflicts"] == 1


def test_completed_historical_parent_without_child_is_diagnosed_without_sending(tmp_path):
    queue = GraphSyncQueue(None, tmp_path / "queue.sqlite3", auto_start=False)
    parent = parent_job(queue, tmp_path)
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET status='complete',result_json=? WHERE id=?",
                   (json.dumps({"phase": "mail_accepted", "mail": {"accepted": True}}), parent["id"]))
    assert queue.summary()["teams_missing"] == 1
    assert queue.status(parent["id"])["status"] == "complete"
    queue.run_until_idle()
    assert child_rows(queue) == [] and queue.summary()["teams_missing"] == 1


def test_status_reconciliation_is_admitted_transactional_and_idempotent_under_concurrent_reads(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from src.services.runtime_contract import RuntimeContract, ContractBusy, ContractError, SUPPORTED_FORMATS
    contract = RuntimeContract(tmp_path / "data", "a" * 64)
    contract.initialize_empty()
    queue = GraphSyncQueue(None, contract.data_dir / "queue.sqlite3", runtime_contract=contract, auto_start=False)
    parent = parent_job(queue, tmp_path)
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET status='running',updated_at=0,result_json=? WHERE id=?",
                   (json.dumps({"mail": {"accepted": True}, "phase": "mail_accepted"}), parent["id"]))
    ensure_child = queue._ensure_teams_child
    transactions = []
    def inspect(connection, *args):
        transactions.append(connection.in_transaction)
        with pytest.raises(ContractBusy):
            contract.activate("b" * 64, next_formats=SUPPORTED_FORMATS)
        return ensure_child(connection, *args)
    monkeypatch.setattr(queue, "_ensure_teams_child", inspect)
    with ThreadPoolExecutor(max_workers=2) as pool:
        states = list(pool.map(lambda _: queue.status(parent["id"]), range(2)))
    assert all(state["status"] == "complete" for state in states)
    assert transactions == [True] and len(child_rows(queue)) == 1
    contract.activate("b" * 64, next_formats=SUPPORTED_FORMATS)
    with pytest.raises(ContractError):
        queue.status(parent["id"])
    assert queue.summary()["complete"] == 1  # genuinely read-only diagnostic remains available
