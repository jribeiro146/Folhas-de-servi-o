"""Actual serialized envelope and byte limits, before credentials or POST."""
from dataclasses import replace
import json

import pytest

from src.services.graph_mail_service import GraphMailService, GraphMailError, GraphMailConfigurationError
from tests.test_graph_mail_service import mail_config
from tests.test_delivery_boundaries import Response
from src.services.graph_sync_queue import GraphSyncQueue
from types import SimpleNamespace

LIMIT = 4_000_000


def envelope():
    return {"message": {"toRecipients": [{"emailAddress": {"address": "sandbox@example.invalid"}}]}}


def service(calls, *, test=False):
    def transport(req, timeout):
        calls.append(req)
        return Response()
    config = replace(mail_config(), test_recipient="  sandbox@example.invalid  " if test else "")
    return GraphMailService(config, transport=transport)


@pytest.mark.parametrize("key", ["toRecipients", "ccRecipients", "bccRecipients"])
def test_raw_envelope_cannot_bypass_test_recipient_in_any_field(key):
    calls = []
    client = service(calls, test=True)
    payload = envelope()
    payload["message"][key] = [{"emailAddress": {"address": "outside@example.invalid"}}]
    with pytest.raises(GraphMailConfigurationError):
        client._graph_bytes("POST", "/users/service@sensorpoint.pt/sendMail", data=json.dumps(payload).encode())
    assert calls == []


@pytest.mark.parametrize("payload", [[], {}, {"message": []}, {"message": {"toRecipients": []}},
    {"message": {"toRecipients": "not a list"}},
    {"message": {"toRecipients": [{"emailAddress": {"address": "bad"}}]}},
    {"message": {"toRecipients": [{"emailAddress": "not an object"}]}}])
def test_malformed_actual_envelope_is_refused_before_token(payload):
    calls = []
    client = service(calls)
    with pytest.raises(GraphMailConfigurationError):
        client._graph_bytes("POST", "/users/service@sensorpoint.pt/sendMail", data=json.dumps(payload).encode())
    assert calls == []


def test_total_serialized_bytes_include_utf8_body_and_base64_attachment(tmp_path):
    calls = []
    client = service(calls)
    pdf = tmp_path / "synthetic.pdf"
    pdf.write_bytes(b"%PDF-synthetic")
    prepared = {"to": "customer@example.invalid", "body_html": "á" * (LIMIT // 2)}
    with pytest.raises(GraphMailError, match="4 M"):
        client.send_prepared(prepared, pdf, operation_id="synthetic")
    assert calls == []
    # A PDF allowed by the raw-file limit is still too large after base64 + JSON.
    pdf.write_bytes(b"%PDF-" + b"x" * (3 * 1024 * 1024 - 5))
    with pytest.raises(GraphMailError, match="4 M"):
        client.send_prepared({"to": "customer@example.invalid"}, pdf, operation_id="synthetic")
    assert calls == []


def test_exact_serialized_limit_and_one_byte_over_are_distinguished():
    calls = []
    client = service(calls, test=True)
    raw = json.dumps(envelope()).encode()
    boundary = raw + b" " * (LIMIT - len(raw))
    with pytest.raises(GraphMailError, match="4 M"):
        client._graph_bytes("POST", "/users/service@sensorpoint.pt/sendMail", data=boundary + b" ")
    assert calls == []
    client._graph_bytes("POST", "/users/service@sensorpoint.pt/sendMail", data=boundary)
    assert len(calls) == 2  # token, then the exact payload
    assert calls[1].data == boundary


def test_normal_production_envelope_keeps_external_customer_and_technician_cc(tmp_path):
    calls = []
    client = service(calls)
    pdf = tmp_path / "synthetic.pdf"
    pdf.write_bytes(b"%PDF-synthetic")
    prepared = {"to": "customer@example.invalid", "technician_email": "tech@example.invalid",
                "cc": ["old-manager@example.invalid"], "bcc": ["old-hidden@example.invalid"]}
    assert client.send_prepared(prepared, pdf, operation_id="synthetic")["accepted"] is True
    message = json.loads(calls[-1].data)["message"]
    assert message["toRecipients"] == [{"emailAddress": {"address": "customer@example.invalid"}}]
    assert message["ccRecipients"] == [{"emailAddress": {"address": "tech@example.invalid"}}]
    assert "bccRecipients" not in message


@pytest.mark.parametrize("data", [None, b"not json", b"\xff", "not bytes",
                                  json.dumps(envelope()).encode("utf-16"), b"[" * 5000 + b"]" * 5000],
                         ids=["missing", "not-json", "invalid-utf8", "not-bytes", "utf16", "deep-json"])
def test_invalid_encoded_payload_is_a_controlled_refusal(data):
    calls = []
    client = service(calls)
    with pytest.raises(GraphMailConfigurationError):
        client._graph_bytes("POST", "/users/service@sensorpoint.pt/sendMail", data=data)
    assert calls == []


@pytest.mark.parametrize("path", ["/users/service@sensorpoint.pt/sendMail", "/users/service@sensorpoint.pt/sendmail/", "/users/service@sensorpoint.pt/%73endMail"])
def test_normalized_send_path_cannot_skip_validation_even_with_cached_token(path):
    calls = []
    client = service(calls, test=True)
    client._access_token = "synthetic-cached"
    client._access_token_expires_at = float("inf")
    payload = envelope()
    payload["message"]["bccRecipients"] = [{"emailAddress": {"address": "outside@example.invalid"}}]
    with pytest.raises(GraphMailConfigurationError):
        client._graph_bytes("POST", path, data=json.dumps(payload).encode())
    assert calls == []


@pytest.mark.parametrize("path", ["/$batch", "/users/service@sensorpoint.pt/messages/m/send",
    "/users/service@sensorpoint.pt/messages/m/forward", "/users/service@sensorpoint.pt/messages/m/replyAll",
    "/users/service@sensorpoint.pt/sendMail;p", "/users/other@example.invalid/sendMail"])
def test_other_writes_are_rejected_even_with_a_valid_test_envelope(path):
    calls = []
    client = service(calls, test=True)
    with pytest.raises(GraphMailConfigurationError):
        client._graph_bytes("POST", path, data=json.dumps(envelope()).encode())
    assert calls == []


@pytest.mark.parametrize("raw", [
    b'{"message":{"toRecipients":[{"emailAddress":{"address":"sandbox@example.invalid"}}],"bccRecipients":[{"emailAddress":{"address":"outside@example.invalid"}}],"bccRecipients":[]}}',
    b'{"message":{"toRecipients":[{"emailAddress":{"address":"sandbox@example.invalid"}}],"BccRecipients":[{"emailAddress":{"address":"outside@example.invalid"}}]}}',
    b'{"message":{"toRecipients":[{"emailAddress":{"address":"sandbox@example.invalid","Address":"outside@example.invalid"}}]}}',
])
def test_ambiguous_json_members_are_rejected_without_io(raw):
    calls = []
    client = service(calls, test=True)
    with pytest.raises(GraphMailConfigurationError):
        client._graph_bytes("POST", "/users/service@sensorpoint.pt/sendMail", data=raw)
    assert calls == []


@pytest.mark.parametrize("member", ["from", "sender", "replyTo", "unknown"])
def test_unexpected_message_fields_are_rejected(member):
    calls = []
    client = service(calls, test=True)
    payload = envelope()
    payload["message"][member] = {"emailAddress": {"address": "outside@example.invalid"}}
    with pytest.raises(GraphMailConfigurationError):
        client._graph_bytes("POST", "/users/service@sensorpoint.pt/sendMail", data=json.dumps(payload).encode())
    assert calls == []


@pytest.mark.parametrize("url, method, authenticated", [
    ("https://outside.invalid/v1.0/users/service@sensorpoint.pt/sendMail", "POST", True),
    ("http://graph.microsoft.com/v1.0/users/service@sensorpoint.pt/sendMail", "POST", True),
    ("https://graph.microsoft.com/v1.0/users/service@sensorpoint.pt/sendMail?extra=1", "POST", True),
    ("https://graph.microsoft.com/v1.0/users/service@sensorpoint.pt/sendMail", "GET", True),
    ("https://graph.microsoft.com/v1.0/users/service@sensorpoint.pt/sendMail", "POST", False),
    ("https://login.microsoftonline.com/other/oauth2/v2.0/token", "POST", False),
    ("https://login.microsoftonline.com/tenant/oauth2/v2.0/token", "POST", True),
])
def test_only_exact_authorized_routes_and_auth_modes_are_allowed(url, method, authenticated):
    calls = []
    client = service(calls)
    with pytest.raises(GraphMailConfigurationError):
        client._request_bytes(url, method=method, authenticated=authenticated, data=json.dumps(envelope()).encode())
    assert calls == []


def test_oversized_message_is_a_known_refusal_in_the_queue(tmp_path):
    calls = []
    client = service(calls)
    pdf = tmp_path / "synthetic.pdf"
    pdf.write_bytes(b"%PDF-" + b"x" * (3 * 1024 * 1024 - 5))
    queue = GraphSyncQueue(None, tmp_path / "queue.sqlite3", mail_service=client,
                          local_pdf_service=SimpleNamespace(export_archive_pdf=lambda path: pdf), auto_start=False)
    job = queue.enqueue("archive_and_mail_local", {"archived_path": str(tmp_path / "synthetic.xlsx"),
                                                 "mail": {"to": "customer@example.invalid"}})
    queue.run_until_idle()
    state = queue.status(job["id"])
    assert state["status"] == "failed"
    assert state["result"]["phase"] == "mail_rejected"
    assert state["will_retry"] is False
    assert state["reconciliation_required"] is False
    assert calls == []
