"""Lost send responses and safe refusals, with synthetic transports only."""
import io
import json
import time
from http.client import RemoteDisconnected
from urllib.error import URLError, HTTPError

import pytest

from src.services.graph_mail_service import (GraphMailService, GraphMailConfig,
    GraphMailUnknownResultError, GraphMailTransientError)
from src.services.graph_sync_queue import GraphSyncQueue


def queue_with_mail(tmp_path, monkeypatch, transport):
    pdf = tmp_path / "synthetic.pdf"
    pdf.write_bytes(b"%PDF-1.4 synthetic")
    class PDF:
        def export_archive_pdf(self, _):
            return pdf
    mail = GraphMailService(GraphMailConfig("synthetic", "synthetic", "synthetic", "sender@example.invalid"),
                            transport=transport)
    monkeypatch.setattr(mail, "_access_token_value", lambda: "synthetic")
    queue = GraphSyncQueue(None, tmp_path / "queue.sqlite3", mail_service=mail,
                           local_pdf_service=PDF(), auto_start=False)
    job = queue.enqueue("archive_and_mail_local", {"archived_path": str(tmp_path / "synthetic.xlsx"),
        "mail": {"to": "recipient@example.invalid", "subject": "[TESTE] synthetic"}})
    return queue, job


@pytest.mark.parametrize("failure", [URLError("response lost"), TimeoutError("timeout"),
    RemoteDisconnected("response lost"), HTTPError("https://synthetic.invalid", 503, "unavailable", {}, io.BytesIO(b"{}"))])
def test_accepted_send_with_lost_response_never_repeats(tmp_path, monkeypatch, failure):
    accepted = []
    def transport(req, **_):
        accepted.append(json.loads(req.data))
        raise failure  # Simulated acceptance before loss of the response.
    queue, job = queue_with_mail(tmp_path, monkeypatch, transport)
    queue.run_until_idle()
    state = queue.status(job["id"])
    assert state["status"] == "failed" and not state["will_retry"]
    assert state["reconciliation_required"]
    with pytest.raises(ValueError, match="reconciliação"):
        queue.retry(job["id"])
    restarted = GraphSyncQueue(None, queue.database_path, auto_start=False)
    restarted.run_until_idle()
    assert restarted.status(job["id"])["reconciliation_required"]
    assert len(accepted) == 1


def test_explicit_throttling_can_retry_without_uncertainty(tmp_path, monkeypatch):
    calls = []
    def transport(req, **_):
        calls.append(req)
        if len(calls) == 1:
            raise HTTPError(req.full_url, 429, "throttled", {"Retry-After": "7"}, io.BytesIO(b"{}"))
        return io.BytesIO(b"")
    queue, job = queue_with_mail(tmp_path, monkeypatch, transport)
    queue.run_until_idle()
    state = queue.status(job["id"])
    assert state["will_retry"] and not state["reconciliation_required"]
    assert state["result"]["phase"] == "mail_rejected"
    assert state["next_attempt_at"] >= time.time() + 5
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET next_attempt_at=0 WHERE id=?", (job["id"],))
    queue.run_until_idle()
    assert queue.status(job["id"])["result"]["mail"]["accepted"]
    assert len(calls) == 2


@pytest.mark.parametrize("phase", ["sending_mail", "mail_unknown"])
def test_recovery_does_not_erase_uncertainty_with_pdf_checkpoint(tmp_path, monkeypatch, phase):
    def no_send(*_, **__):
        pytest.fail("No send before reconciliation")
    queue, job = queue_with_mail(tmp_path, monkeypatch, no_send)
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET status='failed', retryable=1, next_attempt_at=0, result_json=? WHERE id=?",
                   (json.dumps({"phase": phase}), job["id"]))
    queue.run_until_idle()
    state = queue.status(job["id"])
    assert state["reconciliation_required"] and state["result"]["phase"] == phase
    assert not state["will_retry"]


def test_token_failure_is_safe_to_retry_before_send(monkeypatch):
    def unavailable(*_, **__):
        raise URLError("token service unavailable")
    service = GraphMailService(GraphMailConfig("synthetic", "synthetic", "synthetic", "sender@example.invalid"),
                               transport=unavailable)
    with pytest.raises(GraphMailTransientError):
        service._request_bytes("https://login.microsoftonline.com/synthetic/oauth2/v2.0/token", method="POST", authenticated=False)


def test_unknown_runtime_error_after_post_is_not_an_automatic_retry(tmp_path, monkeypatch):
    def failure(*_, **__):
        raise RuntimeError("unexpected response failure")
    queue, job = queue_with_mail(tmp_path, monkeypatch, failure)
    queue.run_until_idle()
    assert queue.status(job["id"])["reconciliation_required"]
    assert not queue.status(job["id"])["will_retry"]
