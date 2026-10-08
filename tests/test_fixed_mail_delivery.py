"""A final message can be checked and sent without reopening its source PDF."""
import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

import src.services.graph_mail_service as mail
from tests.test_delivery_boundaries import Response
from tests.test_graph_mail_service import mail_config


def test_fixed_delivery_survives_source_changes_without_changing_sent_bytes(tmp_path):
    calls = []
    service = mail.GraphMailService(mail_config(), transport=lambda req, **kw: calls.append(req) or Response())
    pdf = tmp_path / "synthetic.pdf"
    pdf.write_bytes(b"%PDF-synthetic-original")
    prepared = {"to": "customer@example.invalid", "technician_email": "tech@example.invalid",
                "subject": "Synthetic original", "body_html": "<p>Original</p>"}
    delivery = service.prepare_delivery(prepared, pdf, operation_id="synthetic:fixed")
    assert calls == []
    with pytest.raises(FrozenInstanceError):
        delivery.payload = b"changed"
    prepared.update(to="changed@example.invalid", subject="Changed", body_html="Changed")
    pdf.unlink()
    service.validate_delivery(delivery)
    assert calls == []
    receipt = service.send_delivery(delivery)
    sends = [req for req in calls if req.full_url.endswith("/sendMail")]
    assert len(sends) == 1
    assert sends[0].data == delivery.payload
    message = json.loads(sends[0].data)["message"]
    assert message["subject"] == "Synthetic original"
    assert message["body"]["content"] == "<p>Original</p>"
    assert receipt == {"accepted": True, "sender": "service@sensorpoint.pt",
                       "to": "customer@example.invalid", "cc": ["tech@example.invalid"],
                       "attachment": "synthetic.pdf"}


def test_preparation_limits_pdf_read_before_allocating_an_entire_large_file(tmp_path, monkeypatch):
    service = mail.GraphMailService(mail_config(), transport=lambda *args, **kw: pytest.fail("No I/O"))
    pdf = tmp_path / "large.pdf"
    pdf.write_bytes(b"%PDF-" + b"x" * (mail.MAX_SIMPLE_ATTACHMENT_BYTES + 50))
    original_open = Path.open
    reads = []

    class BoundedReader:
        def __init__(self, file):
            self.file = file

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.file.close()

        def read(self, size=-1):
            reads.append(size)
            assert 0 < size <= mail.MAX_SIMPLE_ATTACHMENT_BYTES + 1
            return self.file.read(size)

    monkeypatch.setattr(Path, "open", lambda path, *args, **kw:
                        BoundedReader(original_open(path, *args, **kw)) if path == pdf
                        else original_open(path, *args, **kw))
    with pytest.raises(mail.GraphMailError, match="3 MB"):
        service.prepare_delivery({"to": "customer@example.invalid"}, pdf, operation_id="large")
    assert reads == [mail.MAX_SIMPLE_ATTACHMENT_BYTES + 1]


def test_preparation_checks_live_policy_without_network_io(tmp_path, monkeypatch):
    pdf = tmp_path / "synthetic.pdf"
    pdf.write_bytes(b"%PDF-synthetic")
    monkeypatch.setenv("FS_ENVIRONMENT", "test")
    monkeypatch.setattr(mail, "MAIL_ENABLED", True)
    monkeypatch.setattr(mail.request, "urlopen", lambda *a, **kw: pytest.fail("No network"))
    with pytest.raises(mail.GraphMailConfigurationError):
        mail.GraphMailService(mail_config()).prepare_delivery(
            {"to": "customer@example.invalid"}, pdf, operation_id="blocked")


def fixed_delivery(tmp_path):
    pdf = tmp_path / "synthetic.pdf"
    pdf.write_bytes(b"%PDF-synthetic")
    service = mail.GraphMailService(mail_config(), transport=lambda *a, **kw: pytest.fail("No I/O"))
    return service.prepare_delivery({"to": "customer@example.invalid"}, pdf, operation_id="fixed")


@pytest.mark.parametrize("changes", [{"sender": "other@example.invalid"}, {"tenant_id": "another-tenant"},
                                     {"test_recipient": "sandbox@example.invalid"}])
def test_configuration_change_refuses_fixed_delivery_without_rewriting_it(tmp_path, changes):
    delivery = fixed_delivery(tmp_path)
    original = delivery.payload
    service = mail.GraphMailService(replace(mail_config(), **changes),
                                   transport=lambda *a, **kw: pytest.fail("No I/O"))
    with pytest.raises(mail.GraphMailConfigurationError):
        service.send_delivery(delivery)
    assert delivery.payload == original


def test_policy_is_rechecked_after_preparation(tmp_path, monkeypatch):
    delivery = fixed_delivery(tmp_path)
    monkeypatch.setenv("FS_ENVIRONMENT", "production")
    monkeypatch.setattr(mail, "MAIL_ENABLED", True)
    service = mail.GraphMailService(mail_config())
    service.validate_delivery(delivery)
    monkeypatch.setattr(mail, "MAIL_ENABLED", False)
    monkeypatch.setattr(mail.request, "urlopen", lambda *a, **kw: pytest.fail("No network"))
    with pytest.raises(mail.GraphMailConfigurationError):
        service.send_delivery(delivery)


@pytest.mark.parametrize("broken", ["name", "missing_attachment", "extra_attachment", "base64", "pdf",
                                    "subject", "body", "header", "sent_items"])
def test_incomplete_fixed_message_is_rejected_before_any_delivery(tmp_path, broken):
    delivery = fixed_delivery(tmp_path)
    payload = json.loads(delivery.payload)
    message = payload["message"]
    if broken == "name":
        message["attachments"][0]["name"] = None
    elif broken == "missing_attachment":
        message.pop("attachments")
    elif broken == "extra_attachment":
        message["attachments"].append(message["attachments"][0].copy())
    elif broken == "base64":
        message["attachments"][0]["contentBytes"] = "not base64"
    elif broken == "pdf":
        message["attachments"][0]["contentBytes"] = "SGVsbG8="
    elif broken == "subject":
        message["subject"] = []
    elif broken == "body":
        message["body"] = {"contentType": "HTML", "content": []}
    elif broken == "header":
        message["internetMessageHeaders"] = []
    else:
        payload["saveToSentItems"] = False
    invalid = replace(delivery, payload=json.dumps(payload).encode("utf-8"))
    service = mail.GraphMailService(mail_config(), transport=lambda *a, **kw: pytest.fail("No I/O"))
    with pytest.raises(mail.GraphMailConfigurationError):
        service.send_delivery(invalid)


@pytest.mark.parametrize("initial, current", [("sandbox@example.invalid", ""),
    ("sandbox@example.invalid", "other@example.invalid"), ("", "customer@example.invalid")])
def test_fixed_message_binds_test_mode_in_both_directions(tmp_path, initial, current):
    pdf = tmp_path / "synthetic.pdf"
    pdf.write_bytes(b"%PDF-synthetic")
    calls = []
    transport = lambda req, **kw: calls.append(req) or Response()
    service = mail.GraphMailService(replace(mail_config(), test_recipient=initial), transport=transport)
    delivery = service.prepare_delivery({"to": "customer@example.invalid"}, pdf, operation_id="mode")
    changed = mail.GraphMailService(replace(mail_config(), test_recipient=current), transport=transport)
    with pytest.raises(mail.GraphMailConfigurationError):
        changed.send_delivery(delivery)
    assert calls == []


@pytest.mark.parametrize("test_box", ["", "sandbox@example.invalid"])
def test_legacy_whitespace_subject_uses_default_and_empty_html_remains_allowed(tmp_path, test_box):
    pdf = tmp_path / "synthetic.pdf"
    pdf.write_bytes(b"%PDF-synthetic")
    calls = []
    service = mail.GraphMailService(replace(mail_config(), test_recipient=test_box),
                                   transport=lambda req, **kw: calls.append(req) or Response())
    service.send_prepared({"to": "customer@example.invalid", "subject": "  \t ", "body_html": ""},
                          pdf, operation_id="legacy")
    message = json.loads(calls[-1].data)["message"]
    assert message["subject"] == ("[TESTE] " if test_box else "") + "Folha de serviço nº - Sensorpoint"
    assert message["body"] == {"contentType": "HTML", "content": ""}


def test_test_identity_normalization_and_secret_rotation_preserve_fixed_bytes(tmp_path):
    pdf = tmp_path / "synthetic.pdf"
    pdf.write_bytes(b"%PDF-synthetic")
    calls = []
    transport = lambda req, **kw: calls.append(req) or Response()
    service = mail.GraphMailService(replace(mail_config(), test_recipient=" Sandbox@Example.Invalid "),
                                   transport=transport)
    delivery = service.prepare_delivery({"to": "customer@example.invalid"}, pdf, operation_id="rotate")
    assert delivery.test_recipient == "sandbox@example.invalid"
    other = mail.GraphMailService(replace(mail_config(), test_recipient="sandbox@example.invalid",
                                         client_secret="synthetic-rotated"), transport=transport)
    assert other.send_delivery(delivery)["accepted"] is True
    assert calls[-1].data == delivery.payload
