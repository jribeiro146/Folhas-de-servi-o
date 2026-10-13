"""Administrative reconciliation uses synthetic provider evidence and no network."""
import base64
import copy
import hashlib
import json
import sqlite3
import time
from types import SimpleNamespace

import pytest

from src.services import admin_access
from src.services.file_mutex import file_mutex
from src.services.graph_mail_service import GraphMailUnknownResultError
from src.services.graph_sync_queue import GraphSyncQueue
from src.services.job_reconciliation import _encoded
from tests.fixed_mail_fake import FixedMailFake
from tests.test_durable_mail import Graph
from tests.test_durable_teams import NOTICE
from tests.test_operational_admin import ADMIN, login


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    monkeypatch.setattr(admin_access, "ADMIN_TENANT_ID", ADMIN.tenant_id)
    monkeypatch.setattr(admin_access, "ADMIN_OBJECT_ID", ADMIN.id)


class UnknownMail(FixedMailFake):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.accept = False

    def send_delivery(self, delivery, *, request_id=None):
        self.validate_delivery(delivery)
        self.calls.append(delivery)
        if not self.accept:
            raise GraphMailUnknownResultError("Synthetic lost response")
        return {"accepted": True}


def mail_job(root):
    mail = UnknownMail()
    queue = GraphSyncQueue(Graph(root), root / "queue.sqlite3", mail_service=mail, auto_start=False)
    job = queue.enqueue("archive_and_remove", {"archived_path": str(root / "synthetic.xlsx"),
        "source_name": "synthetic.xlsx", "expected_etag": "v1", "teams": NOTICE,
        "mail": {"to": "customer@example.invalid", "subject": "Synthetic reconciliation"}}, job_id="send:synthetic")
    queue.run_until_idle()
    assert queue.status(job["id"])["reconciliation_required"]
    return queue, job["id"], mail


def evidence(queue, job_id, decision="accepted"):
    delivery = queue.mail_service.calls[0]
    message = json.loads(delivery.payload)["message"]
    record = {"operation_id": job_id, "outcome": decision, "observed_at": time.time(),
              "request_id": queue.status(job_id)["result"].get("delivery_request_id"),
              "provider_record_id": "provider-synthetic-123", "sender": delivery.sender,
              "to": [x["emailAddress"]["address"] for x in message["toRecipients"]],
              "cc": [x["emailAddress"]["address"] for x in message.get("ccRecipients", [])],
              "subject": message["subject"], "is_draft": False,
              "attachment_sha256": hashlib.sha256(base64.b64decode(message["attachments"][0]["contentBytes"])).hexdigest(),
              "http_status": 400, "payload_sha256": hashlib.sha256(delivery.payload).hexdigest()}
    return {"source": "exchange_sent_message" if decision == "accepted" else "provider_response",
            "reference": "synthetic-provider-record", "reviewed_from_provider": True, "record": record}


def decide(queue, job_id, decision, proof=None, **kwargs):
    return queue.reconcile(job_id, actor=ADMIN, expected_revision=queue.reconciliation(job_id, actor=ADMIN)["revision"],
                           decision=decision, reason="Synthetic record reviewed", evidence=proof, **kwargs)


def persisted(queue):
    with queue._connect() as db:
        return ([tuple(row) for row in db.execute("SELECT * FROM graph_sync_jobs ORDER BY id")],
                [tuple(row) for row in db.execute("SELECT * FROM graph_mail_messages")])


def test_accepted_mail_commits_audit_and_teams_without_replaying_mail(tmp_path):
    queue, job, mail = mail_job(tmp_path)
    proof = evidence(queue, job)
    result = decide(queue, job, "accepted", proof)
    assert result["status"] == "complete" and result["applied"]
    state = queue.status(job)
    assert state["result"]["mail"]["accepted"] and not state["reconciliation_required"]
    event = state["result"]["reconciliation_history"][0]
    assert event["actor"]["object_id"] == ADMIN.id
    assert event["evidence"]["sha256"] == hashlib.sha256(_encoded(proof)).hexdigest()
    assert queue.status("teams:" + job)["status"] == "pending"
    with queue._connect() as db:
        assert db.execute("SELECT payload FROM graph_mail_messages").fetchone()[0] is None
    queue.run_until_idle()
    assert len(mail.calls) == 1
    with pytest.raises(ValueError):
        queue.retry(job)


def test_rejected_mail_requires_separate_retry_and_reuses_fixed_message(tmp_path):
    queue, job, mail = mail_job(tmp_path)
    decide(queue, job, "rejected", evidence(queue, job, "rejected"))
    assert queue.status(job)["status"] == "failed" and not queue.status(job)["will_retry"]
    assert not queue.status(job)["reconciliation_required"]
    queue.run_until_idle()
    assert len(mail.calls) == 1
    mail.accept = True
    queue.retry(job)
    queue.run_until_idle()
    assert len(mail.calls) == 2 and mail.calls[0] == mail.calls[1]
    assert len(queue.status(job)["result"]["reconciliation_history"]) == 1


def test_indeterminate_keeps_bytes_and_hold_then_can_accept_valid_evidence(tmp_path):
    queue, job, mail = mail_job(tmp_path)
    blob = persisted(queue)[1][0][1]
    assert not decide(queue, job, "indeterminate")["applied"]
    assert queue.status(job)["reconciliation_required"] and persisted(queue)[1][0][1] == blob
    with pytest.raises(ValueError):
        queue.retry(job)
    decide(queue, job, "accepted", evidence(queue, job))
    assert [e["decision"] for e in queue.status(job)["result"]["reconciliation_history"]] == ["indeterminate", "accepted"]
    assert len(mail.calls) == 1


@pytest.mark.parametrize("damage", ["none", "origin", "reference", "operation", "period", "record_id", "to", "cc", "subject", "pdf", "draft", "source"])
def test_insufficient_or_mismatched_evidence_preserves_everything(tmp_path, damage):
    queue, job, _ = mail_job(tmp_path)
    proof = evidence(queue, job)
    if damage == "none": proof = None
    elif damage == "origin": proof["reviewed_from_provider"] = False
    elif damage == "reference": proof["reference"] = ""
    elif damage == "source": proof["source"] = "unverified_search"
    else:
        key, value = {"operation": ("operation_id", "different"), "period": ("observed_at", 0),
                      "record_id": ("provider_record_id", ""), "to": ("to", ["other@example.invalid"]),
                      "cc": ("cc", ["other@example.invalid"]), "subject": ("subject", "other"),
                      "pdf": ("attachment_sha256", "0" * 64), "draft": ("is_draft", True)}[damage]
        proof["record"][key] = value
    before = persisted(queue)
    with pytest.raises(ValueError):
        decide(queue, job, "accepted", proof)
    assert persisted(queue) == before


@pytest.mark.parametrize("flag", ["invalid_state", "restore_uncertain", "migration_uncertain", "commit_guard_required"])
def test_reconciliation_does_not_bypass_other_recovery_holds(tmp_path, flag):
    queue, job, _ = mail_job(tmp_path)
    state = queue.status(job)["result"]
    state[flag] = True
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET result_json=? WHERE id=?", (json.dumps(state), job))
    before = persisted(queue)
    with pytest.raises(ValueError):
        decide(queue, job, "accepted", evidence(queue, job))
    assert persisted(queue) == before
    assert not decide(queue, job, "indeterminate")["applied"]


def test_changed_snapshot_or_stale_revision_is_refused(tmp_path):
    queue, job, _ = mail_job(tmp_path)
    old = queue.reconciliation(job, actor=ADMIN)["revision"]
    decide(queue, job, "indeterminate")
    before = persisted(queue)
    with pytest.raises(ValueError, match="mudou"):
        queue.reconcile(job, actor=ADMIN, expected_revision=old, decision="accepted", reason="Synthetic reason", evidence=evidence(queue, job))
    assert persisted(queue) == before
    with queue._connect() as db:
        db.execute("UPDATE graph_mail_messages SET payload=?", (b"tampered",))
    before = persisted(queue)
    with pytest.raises(ValueError):
        decide(queue, job, "accepted", evidence(queue, job))
    assert persisted(queue) == before


def test_live_worker_or_other_identity_cannot_reconcile(tmp_path):
    queue, job, _ = mail_job(tmp_path)
    before = persisted(queue)
    with file_mutex(queue._worker_mutex_path), pytest.raises(ValueError, match="worker"):
        decide(queue, job, "indeterminate")
    with pytest.raises(PermissionError):
        queue.reconciliation(job, actor=SimpleNamespace(email=ADMIN.email, id="other", tenant_id=ADMIN.tenant_id))
    assert persisted(queue) == before


def test_failed_transaction_rolls_back_receipt_audit_retirement_and_child(tmp_path, monkeypatch):
    queue, job, _ = mail_job(tmp_path)
    before = persisted(queue)
    connect = sqlite3.connect
    class FailAfterEffects(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.startswith("UPDATE graph_sync_jobs SET result_json=?,status="):
                raise sqlite3.OperationalError("synthetic transaction failure")
            return super().execute(sql, parameters)
    def failing_connect(*args, **kwargs):
        kwargs["factory"] = FailAfterEffects
        return connect(*args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", failing_connect)
    with pytest.raises(sqlite3.OperationalError):
        decide(queue, job, "accepted", evidence(queue, job))
    assert persisted(queue) == before


def teams_job(root):
    queue = GraphSyncQueue(None, root / "queue.sqlite3", auto_start=False)
    job = queue.enqueue("notify_teams", {"notification": NOTICE}, job_id="teams:synthetic")
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET status='failed',result_json=? WHERE id=?",
                   (json.dumps({"phase": "teams_unknown", "delivery_request_id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd"}), job["id"]))
    return queue, job["id"]


def teams_evidence(job, decision="accepted"):
    return {"source": "workflow_history" if decision == "accepted" else "provider_response",
            "reference": "synthetic-workflow-run", "reviewed_from_provider": True,
            "record": {"operation_id": job, "outcome": decision, "observed_at": time.time(),
                       "request_id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                       "provider_record_id": "run-123", "http_status": 202 if decision == "accepted" else 403,
                       "payload_sha256": hashlib.sha256(_encoded(NOTICE["payload"])).hexdigest()}}


@pytest.mark.parametrize("decision", ["accepted", "rejected"])
def test_teams_resolves_without_any_parent_mail_execution(tmp_path, decision):
    queue, job = teams_job(tmp_path)
    result = decide(queue, job, decision, teams_evidence(job, decision))
    assert result["status"] == ("complete" if decision == "accepted" else "failed")
    assert not queue.status(job)["reconciliation_required"]
    with queue._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM graph_sync_jobs").fetchone()[0] == 1


def test_teams_429_requires_and_keeps_provider_deadline(tmp_path):
    queue, job = teams_job(tmp_path)
    proof = teams_evidence(job, "rejected")
    proof["record"]["http_status"] = 429
    with pytest.raises(ValueError):
        decide(queue, job, "rejected", proof)
    proof["record"]["retry_not_before"] = time.time() + 3600
    decide(queue, job, "rejected", proof)
    with pytest.raises(ValueError, match="prazo"):
        queue.retry(job)


def test_repair_missing_teams_is_independent_and_idempotent(tmp_path):
    queue, job, mail = mail_job(tmp_path)
    decide(queue, job, "accepted", evidence(queue, job))
    with queue._connect() as db:
        db.execute("DELETE FROM graph_sync_jobs WHERE id=?", ("teams:" + job,))
    assert queue.reconciliation(job, actor=ADMIN)["can_repair_teams"]
    for _ in range(2):
        assert decide(queue, job, "repair_teams")["applied"]
    with queue._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM graph_sync_jobs WHERE kind='notify_teams'").fetchone()[0] == 1
    assert len(mail.calls) == 1


def test_cancel_conflict_can_be_recorded_but_wrong_remote_identity_cannot_close_it(tmp_path):
    from tests.test_cancellation_recovery import CancellationGraph, source_with_receipts, plan, guarded_payload
    graph = CancellationGraph()
    queue = GraphSyncQueue(graph, tmp_path / "queue.sqlite3", auto_start=False)
    remote = plan(graph, source_with_receipts(tmp_path))
    _, payload = guarded_payload(tmp_path, remote)
    job = queue.enqueue("remove_active", payload)["id"]
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET status='failed',result_json=? WHERE id=?",
                   (json.dumps({"phase": "canceling_remote", "cancellation_started": True}), job))
    decide(queue, job, "indeterminate")
    assert queue.status(job)["reconciliation_required"]
    proof = {"source": "graph_item", "reference": "synthetic-item-inspection", "reviewed_from_provider": True,
             "record": {"operation_id": job, "outcome": "accepted", "observed_at": time.time(),
                        "provider_record_id": "item-observation", "item_id": "homonym", "drive_id": graph.config.drive_id,
                        "name": remote["destination_name"], "parent_path": "Arquivadas/Canceladas", "parent_id": "canceled-root"}}
    with pytest.raises(ValueError):
        decide(queue, job, "accepted", proof)
    proof["record"]["item_id"] = remote["source_id"]
    assert decide(queue, job, "accepted", proof)["status"] == "complete"
    assert graph.writes == []


def test_routes_require_identity_csrf_and_do_not_export_private_evidence(tmp_path, monkeypatch):
    import src.web.application as web
    from src.services.editing_state_service import EditingStateService
    queue, job, _ = mail_job(tmp_path)
    monkeypatch.setattr(web, "AUTH_ENABLED", True)
    monkeypatch.setattr(web, "ACTIVE_AUTH_PROVIDER", "microsoft")
    class Auth:
        def user_from_session(self, payload): return SimpleNamespace(**payload) if payload else None
    app = web.create_app(editing_state_service=EditingStateService(tmp_path / "edits"), graph_sync_queue=queue, microsoft_auth_service=Auth())
    url = "/api/admin/jobs/" + job + "/reconciliation"
    assert login(app, "other@sensorpoint.pt").get(url).status_code == 403
    client = login(app, ADMIN.email)
    view = client.get(url)
    assert view.status_code == 200 and "no-store" in view.headers["Cache-Control"]
    data = {"revision": view.json["reconciliation"]["revision"], "decision": "accepted", "reason": "Synthetic record reviewed", "evidence": evidence(queue, job)}
    assert client.post(url, json=data).status_code == 403
    response = client.post(url, json=data, headers={"X-Admin-CSRF": "synthetic-csrf"})
    assert response.status_code == 200
    content = response.data + client.get(url).data + client.get("/api/admin/overview").data
    for private in (b"customer@example.invalid", b"sender@example.invalid", b"synthetic-provider-record", b"provider-synthetic-123"):
        assert private not in content


def test_old_refusal_cannot_release_a_new_uncertain_request(tmp_path):
    queue, job, mail = mail_job(tmp_path)
    old = evidence(queue, job, "rejected")
    decide(queue, job, "rejected", old)
    queue.retry(job)
    queue.run_until_idle()
    assert len(mail.calls) == 2 and mail.calls[0] == mail.calls[1]
    assert queue.status(job)["result"]["delivery_request_id"] != old["record"]["request_id"]
    before = persisted(queue)
    with pytest.raises(ValueError, match="último pedido"):
        decide(queue, job, "rejected", old)
    assert persisted(queue) == before and queue.status(job)["reconciliation_required"]


def test_legacy_request_without_correlation_can_only_remain_held_or_prove_acceptance(tmp_path):
    queue, job, _ = mail_job(tmp_path)
    state = queue.status(job)["result"]
    state.pop("delivery_request_id")
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET result_json=? WHERE id=?", (json.dumps(state), job))
    with pytest.raises(ValueError):
        decide(queue, job, "rejected", evidence(queue, job, "rejected"))
    assert decide(queue, job, "accepted", evidence(queue, job))["status"] == "complete"


@pytest.mark.parametrize("status", [408, 425, 500, 503])
def test_ambiguous_http_status_is_not_rejection_evidence(tmp_path, status):
    queue, job, _ = mail_job(tmp_path)
    proof = evidence(queue, job, "rejected")
    proof["record"]["http_status"] = status
    before = persisted(queue)
    with pytest.raises(ValueError):
        decide(queue, job, "rejected", proof)
    assert persisted(queue) == before


def test_reconciled_mail_throttle_also_blocks_premature_manual_retry(tmp_path):
    queue, job, _ = mail_job(tmp_path)
    proof = evidence(queue, job, "rejected")
    proof["record"].update(http_status=429, retry_not_before=time.time() + 3600)
    decide(queue, job, "rejected", proof)
    with pytest.raises(ValueError, match="prazo"):
        queue.retry(job)


def test_mail_request_correlation_reaches_transport_without_changing_fixed_payload(tmp_path):
    from tests.test_durable_mail import make_mail
    queue, job, mail = mail_job(tmp_path)
    calls = []
    transport_mail = make_mail(calls)
    request_id = queue.status(job)["result"]["delivery_request_id"]
    transport_mail.send_delivery(mail.calls[0], request_id=request_id)
    sent = next(req for req in calls if req.full_url.endswith("/sendMail"))
    assert sent.get_header("Client-request-id") == request_id
    assert sent.data == mail.calls[0].payload


def test_teams_request_correlation_is_durable_before_transport(tmp_path):
    from urllib.error import URLError
    from src.services.teams_notification_service import TeamsNotificationService, TeamsNotificationConfig
    queue = None
    seen = []
    def transport(req, **kwargs):
        with queue._connect() as db:
            row = db.execute("SELECT result_json,attempt_token FROM graph_sync_jobs").fetchone()
        request_id = req.get_header("X-sensorpoint-request-id")
        assert json.loads(row[0])["delivery_request_id"] == request_id
        assert request_id != row[1]
        seen.append(request_id)
        raise URLError("synthetic lost reply")
    service = TeamsNotificationService(TeamsNotificationConfig("https://example.invalid/synthetic"), transport=transport)
    queue = GraphSyncQueue(None, tmp_path / "queue.sqlite3", teams_notification_service=service, auto_start=False)
    job = queue.enqueue("notify_teams", {"notification": NOTICE})["id"]
    queue.run_until_idle()
    assert len(seen) == 1 and queue.status(job)["reconciliation_required"]


def test_cancellation_request_correlation_is_durable_and_rejection_preserves_preflight(tmp_path, monkeypatch):
    from tests.test_cancellation_recovery import CancellationGraph, source_with_receipts, plan, guarded_payload
    graph = CancellationGraph()
    remote = plan(graph, source_with_receipts(tmp_path))
    _, payload = guarded_payload(tmp_path, remote)
    queue = GraphSyncQueue(graph, tmp_path / "queue.sqlite3", auto_start=False)
    job = queue.enqueue("remove_active", payload)["id"]
    seen = []
    def lost_response(*args, headers, **kwargs):
        with queue._connect() as db:
            row = db.execute("SELECT result_json,attempt_token FROM graph_sync_jobs WHERE id=?", (job,)).fetchone()
        request_id = headers["client-request-id"]
        assert json.loads(row[0])["delivery_request_id"] == request_id != row[1]
        assert headers["If-Match"] == remote["source_etag"]
        seen.append(request_id)
        raise TimeoutError("Synthetic response lost")
    monkeypatch.setattr(graph, "_graph_bytes", lost_response)
    queue.run_until_idle()
    assert len(seen) == 1 and queue.status(job)["reconciliation_required"]
    proof = {"source": "provider_response", "reference": "synthetic-rejection-record", "reviewed_from_provider": True,
             "record": {"operation_id": job, "outcome": "rejected", "observed_at": time.time(),
                        "provider_record_id": "response-123", "item_id": remote["source_id"], "drive_id": remote["drive_id"],
                        "if_match": remote["source_etag"], "request_id": seen[0], "http_status": 412}}
    decide(queue, job, "rejected", proof)
    assert queue.status(job)["status"] == "failed" and len(seen) == 1
    graph.item["eTag"] = "concurrent-version"
    queue.retry(job)
    queue.run_until_idle()
    assert queue.status(job)["status"] == "failed" and len(seen) == 1


def test_repair_teams_retains_divergent_child_without_reopening_mail(tmp_path):
    queue, job, mail = mail_job(tmp_path)
    decide(queue, job, "accepted", evidence(queue, job))
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET payload_json='{}' WHERE id=?", ("teams:" + job,))
    assert decide(queue, job, "repair_teams")["applied"] is False
    assert queue.status(job)["result"]["mail"]["accepted"] is True
    assert queue.status("teams:" + job)["result"]["invalid_state"] is True
    assert len(mail.calls) == 1


def test_archive_without_mail_cannot_create_an_uncertainty_hold(tmp_path):
    queue = GraphSyncQueue(None, tmp_path / "queue.sqlite3", auto_start=False)
    job = queue.enqueue("archive_and_remove", {"source_name": "synthetic.xlsx"})["id"]
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET status='failed',retryable=0,attempts=1 WHERE id=?", (job,))
    before = persisted(queue)
    assert not queue.reconciliation(job, actor=ADMIN)["can_record"]
    with pytest.raises(ValueError):
        decide(queue, job, "indeterminate")
    assert persisted(queue) == before
    assert queue.retry(job)["status"] in {"pending", "held"}


def test_cancellation_preflight_conflict_cannot_create_an_uncertainty_hold(tmp_path):
    from tests.test_cancellation_recovery import CancellationGraph, source_with_receipts, plan, guarded_payload
    graph = CancellationGraph()
    _, payload = guarded_payload(tmp_path, plan(graph, source_with_receipts(tmp_path)))
    queue = GraphSyncQueue(graph, tmp_path / "queue.sqlite3", auto_start=False)
    job = queue.enqueue("remove_active", payload)["id"]
    graph.item["eTag"] = "concurrent-version"
    queue.run_until_idle()
    assert not graph.writes and queue.status(job)["status"] == "failed"
    before = persisted(queue)
    assert not queue.reconciliation(job, actor=ADMIN)["can_record"]
    with pytest.raises(ValueError):
        decide(queue, job, "indeterminate")
    assert persisted(queue) == before
    assert queue.retry(job)["status"] in {"pending", "held"}


def test_legacy_mail_without_phase_only_offers_indeterminate(tmp_path):
    queue, job, _ = mail_job(tmp_path)
    state = queue.status(job)["result"]
    state.pop("phase")
    state.pop("delivery_request_id")
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET result_json=? WHERE id=?", (json.dumps(state), job))
    before = persisted(queue)
    assert queue.reconciliation(job, actor=ADMIN)["allowed_decisions"] == ["indeterminate"]
    for decision in ("accepted", "rejected"):
        with pytest.raises(ValueError):
            decide(queue, job, decision, evidence(queue, job, decision))
    assert persisted(queue) == before
    decide(queue, job, "indeterminate")
    with pytest.raises(ValueError):
        queue.retry(job)


@pytest.mark.parametrize("kind,mail", [("remove_active", False), ("archive_and_remove", False), ("archive_and_remove", True)])
def test_missing_local_commit_alone_cannot_create_an_uncertainty_hold(tmp_path, kind, mail):
    queue = GraphSyncQueue(None, tmp_path / "queue.sqlite3", auto_start=False)
    payload = {"mail": {"to": "customer@example.invalid"}} if mail else {}
    job = queue.enqueue(kind, payload)["id"]
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET status='failed',retryable=0,attempts=1,result_json=? WHERE id=?",
                   (json.dumps({"commit_guard_required": True}), job))
    before = persisted(queue)
    assert queue.reconciliation(job, actor=ADMIN)["allowed_decisions"] == []
    with pytest.raises(ValueError):
        decide(queue, job, "indeterminate")
    assert persisted(queue) == before
    assert queue.retry(job)["status"] == "held"


def test_missing_commit_with_uncertain_cancellation_still_allows_indeterminate(tmp_path):
    queue = GraphSyncQueue(None, tmp_path / "queue.sqlite3", auto_start=False)
    job = queue.enqueue("remove_active", {})["id"]
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET status='failed',retryable=0,result_json=? WHERE id=?",
                   (json.dumps({"commit_guard_required": True, "cancellation_started": True}), job))
    assert queue.reconciliation(job, actor=ADMIN)["allowed_decisions"] == ["indeterminate"]
    decide(queue, job, "indeterminate")
    with pytest.raises(ValueError):
        queue.retry(job)
