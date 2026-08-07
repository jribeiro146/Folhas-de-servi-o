import json
from pathlib import Path

import pytest

from src.services.graph_mail_service import (
    GraphMailConfig,
    GraphMailError,
    GraphMailService,
)
from src.services.graph_storage_service import GraphConfig, GraphStorageService
from src.services.graph_sync_queue import GraphSyncQueue
from src.services.local_pdf_service import LocalPdfService


def mail_config() -> GraphMailConfig:
    return GraphMailConfig(
        tenant_id="tenant",
        client_id="client",
        client_secret="secret",
        sender="service@sensorpoint.pt",
    )


def test_prepare_service_email_uses_requested_portuguese_body_and_only_technician_cc():
    service = GraphMailService(mail_config())

    prepared = service.prepare_service_email(
        customer_email="cliente@example.com",
        technician_email="tecnico@sensorpoint.pt",
        customer_name="Cliente & Filhos",
        service_number="FS-123",
    )

    assert prepared["to"] == "cliente@example.com"
    assert prepared["cc"] == ["tecnico@sensorpoint.pt"]
    assert prepared["technician_email"] == "tecnico@sensorpoint.pt"
    assert prepared["service_number"] == "FS-123"
    assert prepared["subject"] == "Folha de serviço nº FS-123 - Sensorpoint"
    assert prepared["body_html"] == (
        "<p>Prezado(a) Senhor(a),</p>"
        "<p>Em anexo, encaminhamos a folha de serviço relativa à intervenção "
        "<strong>FS-123</strong>.</p>"
        "<p>Esta comunicação é enviada para um único endereço de e-mail. "
        "Caso considere necessário, agradecemos o seu reencaminhamento interno.</p>"
        "<p>Permanecemos à disposição para qualquer esclarecimento.</p>"
    )


def test_prepare_service_email_uses_fixed_recipient_in_test_mode():
    config = GraphMailConfig(
        tenant_id="tenant",
        client_id="client",
        client_secret="secret",
        sender="jribeiro@sensorpoint.pt",
        test_recipient="jribeiro@sensorpoint.pt",
    )
    service = GraphMailService(config)

    prepared = service.prepare_service_email(
        customer_email="cliente-real@example.com",
        technician_email="tecnico@sensorpoint.pt",
        customer_name="Cliente",
        service_number="FS-TESTE",
    )

    assert prepared["to"] == "jribeiro@sensorpoint.pt"
    assert prepared["test_mode"] is True
    assert prepared["subject"].startswith("[TESTE]")
    assert "cliente-real@example.com" not in json.dumps(prepared)


def test_send_prepared_test_mode_overrides_a_stale_customer_recipient(tmp_path, monkeypatch):
    config = GraphMailConfig(
        tenant_id="tenant",
        client_id="client",
        client_secret="secret",
        sender="jribeiro@sensorpoint.pt",
        test_recipient="jribeiro@sensorpoint.pt",
    )
    service = GraphMailService(config)
    pdf_path = tmp_path / "FS-TESTE__folha_final.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\nconteudo")
    captured = {}

    def fake_graph_bytes(method, path, *, data=None, content_type=None):
        captured["payload"] = json.loads(data.decode("utf-8"))
        return b""

    monkeypatch.setattr(service, "_graph_bytes", fake_graph_bytes)
    result = service.send_prepared(
        {
            "to": "cliente-real@example.com",
            "cc": ["jribeiro@sensorpoint.pt", "dsilva@sensorpoint.pt"],
            "subject": "Folha de serviço",
            "body_html": "<p>Teste</p>",
        },
        pdf_path,
        operation_id="send:stale:test",
    )

    encoded = json.dumps(captured["payload"])
    assert result["to"] == "jribeiro@sensorpoint.pt"
    assert "cliente-real@example.com" not in encoded
    assert captured["payload"]["message"]["subject"].startswith("[TESTE]")
    assert result["cc"] == []


def test_prepare_service_email_rejects_invalid_customer_address():
    service = GraphMailService(mail_config())

    with pytest.raises(GraphMailError, match="cliente"):
        service.prepare_service_email(
            customer_email="endereco-invalido",
            technician_email="tecnico@sensorpoint.pt",
            customer_name="Cliente",
            service_number="FS-123",
        )


def test_send_prepared_posts_pdf_attachment_to_graph(tmp_path, monkeypatch):
    service = GraphMailService(mail_config())
    pdf_path = tmp_path / "FS-123__folha_final.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\nconteudo")
    captured = {}

    def fake_graph_bytes(method, path, *, data=None, content_type=None):
        captured.update(
            method=method,
            path=path,
            payload=json.loads(data.decode("utf-8")),
            content_type=content_type,
        )
        return b""

    monkeypatch.setattr(service, "_graph_bytes", fake_graph_bytes)
    prepared = service.prepare_service_email(
        customer_email="cliente@example.com",
        technician_email="tecnico@sensorpoint.pt",
        customer_name="Cliente",
        service_number="FS-123",
    )

    result = service.send_prepared(prepared, pdf_path, operation_id="send:documento:1")

    assert result["accepted"] is True
    assert captured["method"] == "POST"
    assert captured["path"] == "/users/service@sensorpoint.pt/sendMail"
    message = captured["payload"]["message"]
    assert message["toRecipients"][0]["emailAddress"]["address"] == "cliente@example.com"
    assert [item["emailAddress"]["address"] for item in message["ccRecipients"]] == [
        "tecnico@sensorpoint.pt",
    ]
    assert "bccRecipients" not in message
    assert captured["payload"]["saveToSentItems"] is True
    assert len(message["attachments"]) == 1
    assert message["attachments"][0]["name"] == "FS-123__folha_final.pdf"
    assert message["attachments"][0]["contentType"] == "application/pdf"
    assert message["internetMessageHeaders"][0]["value"] == "send:documento:1"


def test_send_prepared_rejects_an_excel_attachment_even_with_pdf_content(tmp_path):
    service = GraphMailService(mail_config())
    excel_path = tmp_path / "FS-123.xlsx"
    excel_path.write_bytes(b"%PDF-1.7\nconteudo")
    prepared = service.prepare_service_email(
        customer_email="cliente@example.com",
        technician_email="tecnico@sensorpoint.pt",
        customer_name="Cliente",
        service_number="FS-123",
    )

    with pytest.raises(GraphMailError, match="exclusivamente.*PDF"):
        service.send_prepared(prepared, excel_path, operation_id="send:documento:excel")


def test_local_pdf_service_prints_the_final_html_to_pdf(tmp_path):
    excel_path = tmp_path / "FS-123.xlsx"
    excel_path.write_bytes(b"excel")
    html_path = tmp_path / "FS-123__folha_final.html"
    html_path.write_text("<html><body>Folha final</body></html>", encoding="utf-8")

    def fake_renderer(source, destination):
        assert source == html_path
        destination.write_bytes(b"%PDF-1.7\nlocal")

    pdf_path = LocalPdfService(fake_renderer).export_archive_pdf(excel_path)

    assert pdf_path == tmp_path / "FS-123__folha_final.pdf"
    assert pdf_path.read_bytes().startswith(b"%PDF-")


def test_graph_storage_exports_and_uploads_archived_pdf(tmp_path, monkeypatch):
    archive_dir = tmp_path / "Arquivadas" / "FS-123"
    archive_dir.mkdir(parents=True)
    archived_excel = archive_dir / "FS-123.xlsx"
    archived_excel.write_bytes(b"excel")
    service = GraphStorageService(
        GraphConfig(
            tenant_id="tenant",
            client_id="client",
            client_secret="secret",
            drive_id="drive",
            archive_path="Aplicacao/Arquivadas",
        )
    )
    calls = {}

    def fake_graph_bytes(method, path, data=None, content_type=None, headers=None):
        calls["convert"] = (method, path)
        return b"%PDF-1.7\nconvertido"

    def fake_upload_file(local_path, remote_path, *, expected_etag=None):
        calls["upload"] = (Path(local_path), remote_path, expected_etag)
        return {"id": "pdf-id", "name": Path(local_path).name, "eTag": "etag-pdf"}

    monkeypatch.setattr(service, "_graph_bytes", fake_graph_bytes)
    monkeypatch.setattr(service, "upload_file", fake_upload_file)
    monkeypatch.setattr(service, "_write_item_meta", lambda *_args: None)

    pdf_path, remote_pdf = service.export_archive_pdf(archived_excel)

    assert pdf_path.read_bytes().startswith(b"%PDF-")
    assert pdf_path.name == "FS-123__folha_final.pdf"
    assert calls["convert"] == (
        "GET",
        "/drives/drive/root:/Aplicacao/Arquivadas/FS-123/FS-123.xlsx:/content?format=pdf",
    )
    assert remote_pdf == "Aplicacao/Arquivadas/FS-123/FS-123__folha_final.pdf"
    assert calls["upload"][0] == pdf_path


def test_archive_queue_exports_pdf_and_sends_mail_once(tmp_path):
    archive_dir = tmp_path / "Arquivadas" / "FS-123"
    archive_dir.mkdir(parents=True)
    archived_excel = archive_dir / "FS-123.xlsx"
    archived_excel.write_bytes(b"excel")
    events = []

    class FakeGraph:
        def upload_archive_bundle(self, path):
            events.append(("upload", Path(path)))
            return ["Arquivadas/FS-123/FS-123.xlsx"]

        def export_archive_pdf(self, path):
            events.append(("pdf", Path(path)))
            pdf = Path(path).with_name("FS-123__folha_final.pdf")
            pdf.write_bytes(b"%PDF-1.7\nqueue")
            return pdf, "Arquivadas/FS-123/FS-123__folha_final.pdf"

        def remove_active_name(self, name, *, expected_etag=None):
            events.append(("remove", name, expected_etag))
            return True

    class FakeMail:
        def send_prepared(self, prepared, attachment_path, *, operation_id):
            events.append(("mail", prepared["to"], Path(attachment_path), operation_id))
            return {"accepted": True}

    queue = GraphSyncQueue(
        FakeGraph(),
        tmp_path / "graph-sync.sqlite3",
        mail_service=FakeMail(),
    )
    payload = {
        "archived_path": str(archived_excel),
        "source_name": "FS-123",
        "expected_etag": "etag-active",
        "mail": {"to": "cliente@example.com", "cc": [], "subject": "FS", "body_html": "FS"},
    }

    first = queue.enqueue("archive_and_remove", payload, job_id="send:FS-123:stable")
    completed = queue.wait(first["id"], timeout=3)
    replay = queue.enqueue("archive_and_remove", payload, job_id="send:FS-123:stable")

    assert completed["status"] == "complete"
    assert replay["status"] == "complete"
    assert [event[0] for event in events] == ["upload", "pdf", "remove", "mail"]
    assert events[-1][3] == "send:FS-123:stable"
def test_local_archive_queue_generates_pdf_and_sends_without_graph_storage(tmp_path):
    archive_dir = tmp_path / "Arquivadas" / "FS-LOCAL"
    archive_dir.mkdir(parents=True)
    archived_excel = archive_dir / "FS-LOCAL.xlsx"
    archived_excel.write_bytes(b"excel")
    events = []

    class FakeLocalPdf:
        def export_archive_pdf(self, path):
            events.append(("local_pdf", Path(path)))
            pdf = Path(path).with_name("FS-LOCAL__folha_final.pdf")
            pdf.write_bytes(b"%PDF-1.7\nlocal")
            return pdf

    class FakeMail:
        def send_prepared(self, prepared, attachment_path, *, operation_id):
            events.append(("mail", prepared["to"], Path(attachment_path), operation_id))
            return {"accepted": True}

    queue = GraphSyncQueue(
        None,
        tmp_path / "local-mail.sqlite3",
        mail_service=FakeMail(),
        local_pdf_service=FakeLocalPdf(),
    )
    payload = {
        "archived_path": str(archived_excel),
        "mail": {"to": "cliente@example.com", "cc": [], "subject": "FS", "body_html": "FS"},
    }

    first = queue.enqueue("archive_and_mail_local", payload, job_id="send:local:stable")
    completed = queue.wait(first["id"], timeout=3)

    assert completed["status"] == "complete"
    assert [event[0] for event in events] == ["local_pdf", "mail"]
    assert completed["result"]["uploaded_files"] == []
    assert completed["result"]["removed_active"] is False
    assert events[-1][2].suffix == ".pdf"
