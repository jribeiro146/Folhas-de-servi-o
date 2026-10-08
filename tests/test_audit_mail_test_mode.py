"""Test mode must isolate every envelope recipient, including old queued mail."""
import json
from dataclasses import replace
import pytest

from src.services.graph_mail_service import GraphMailService
from tests.test_graph_mail_service import mail_config


@pytest.mark.parametrize("test_mode", [False, True])
def test_prepare_and_actual_send_have_the_same_isolated_recipients(tmp_path, monkeypatch, test_mode):
    config = replace(mail_config(), test_recipient="sandbox@example.invalid" if test_mode else "")
    service = GraphMailService(config, transport=lambda *a, **kw: pytest.fail("Unexpected I/O"))
    prepared = service.prepare_service_email(customer_email="customer@example.invalid",
        technician_email="technician@example.invalid", customer_name="Synthetic", service_number="99")
    assert prepared["cc"] == ([] if test_mode else ["technician@example.invalid"])
    # Simulate recipients frozen by an older application version.
    prepared["to"] = "customer@example.invalid"
    prepared["cc"] = ["technician@example.invalid", "other@example.invalid"]
    pdf = tmp_path / "synthetic.pdf"
    pdf.write_bytes(b"%PDF-synthetic")
    envelopes = []
    monkeypatch.setattr(service, "_graph_bytes", lambda *a, **kw: envelopes.append(json.loads(kw["data"])))
    result = service.send_prepared(prepared, pdf, operation_id="synthetic")
    message = envelopes[0]["message"]
    assert result["cc"] == ([] if test_mode else prepared["cc"][:1])
    assert message["ccRecipients"] == ([] if test_mode else [{"emailAddress": {"address": "technician@example.invalid"}}])
    assert message["toRecipients"][0]["emailAddress"]["address"] == (config.test_recipient if test_mode else prepared["to"])
    assert "bccRecipients" not in message
    assert message["subject"].startswith("[TESTE]") == test_mode


def test_legacy_mail_without_technician_field_has_no_cc_in_test_mode(tmp_path, monkeypatch):
    service = GraphMailService(replace(mail_config(), test_recipient="sandbox@example.invalid"), transport=lambda *a, **kw: pytest.fail("Unexpected I/O"))
    pdf = tmp_path / "synthetic.pdf"
    pdf.write_bytes(b"%PDF-synthetic")
    envelopes = []
    monkeypatch.setattr(service, "_graph_bytes", lambda *a, **kw: envelopes.append(json.loads(kw["data"])))
    service.send_prepared({"to":"customer@example.invalid", "cc":["technician@example.invalid"]}, pdf, operation_id="legacy")
    assert envelopes[0]["message"]["ccRecipients"] == []
