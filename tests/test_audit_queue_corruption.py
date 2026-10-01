"""Malformed durable state is quarantined without poisoning unrelated jobs."""
import json
import time
import pytest

from src.services.graph_sync_queue import GraphSyncQueue


@pytest.mark.parametrize("status", ["pending", "running", "failed", "held"])
@pytest.mark.parametrize("field,raw", [
    ("payload_json", "[]"), ("payload_json", "null"), ("payload_json", "broken"),
    ("payload_json", '{"mail":[]}'), ("result_json", "[]"),
    ("result_json", '{"mail":[]}'), ("result_json", '{"mail":{"accepted":"yes"}}'),
    ("result_json", '{"phase":[]}'),
])
def test_corrupt_job_preserves_evidence_and_does_not_block_valid_work(tmp_path, status, field, raw):
    calls = []
    class Graph:
        def remove_active_name(self, name, **kwargs):
            calls.append(name)
            return True
    queue = GraphSyncQueue(Graph(), tmp_path / "synthetic.sqlite3", auto_start=False)
    bad = queue.enqueue("archive_and_mail_local", {"mail": {"to": "synthetic@example.invalid"}})
    good = queue.enqueue("remove_active", {"source_name": "synthetic.xlsx"})
    with queue._connect() as db:
        db.execute(f"UPDATE graph_sync_jobs SET {field}=?, status=?, updated_at=?, retryable=1 WHERE id=?",
                   (raw, status, time.time() - 7200, bad["id"]))
    # Initialization, status queries and worker processing must all remain usable.
    queue = GraphSyncQueue(Graph(), queue.database_path, auto_start=False)
    before = queue.status(bad["id"])
    assert before["status"] == "failed" and not before["will_retry"]
    assert before["result"]["invalid_state"]
    with pytest.raises(ValueError, match="inválidos"):
        queue.retry(bad["id"])
    queue.run_until_idle()
    assert queue.status(good["id"])["status"] == "complete"
    assert calls == ["synthetic.xlsx"]
    assert queue.status(bad["id"])["result"] == before["result"]
    with queue._connect() as db:
        record = db.execute("SELECT payload_json,result_json FROM graph_sync_jobs WHERE id=?", (bad["id"],)).fetchone()
        if field == "payload_json":
            assert record[field] == raw
        else:
            assert json.loads(record[field])["previous_result_json"] == raw


def test_corruption_introduced_after_initialization_is_isolated_on_claim(tmp_path):
    queue = GraphSyncQueue(None, tmp_path / "synthetic.sqlite3", auto_start=False)
    bad = queue.enqueue("archive_and_mail_local", {"mail": {}})
    good = queue.enqueue("remove_active", {"source_name": "synthetic.xlsx"})
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET payload_json='[]',status='running',updated_at=0 WHERE id=?", (bad["id"],))
    assert queue._claim_next()["id"] == good["id"]
    assert queue.status(bad["id"])["result"]["invalid_state"]
