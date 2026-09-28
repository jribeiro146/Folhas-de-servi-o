import base64
import io
import json
import sqlite3
import subprocess
import time
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

import pytest

from src.config import MAIL_SIGNATURE_PATH
from src.services.graph_mail_service import (
    GraphMailConfig,
    GraphMailConfigurationError,
    GraphMailError,
    GraphMailService,
    GraphMailTransientError,
    GraphMailUnknownResultError,
)
from src.services.graph_storage_service import GraphConfig, GraphStorageService
from src.services.graph_sync_queue import GraphSyncQueue
from src.services.local_pdf_service import LocalPdfError, LocalPdfService


def mail_config() -> GraphMailConfig:
    return GraphMailConfig(
        tenant_id="tenant",
        client_id="client",
        client_secret="secret",
        sender="service@sensorpoint.pt",
    )


def test_prepare_service_email_uses_requested_portuguese_body_and_only_technician_cc():
    service = GraphMailService(mail_config(), transport=simulated_transport)

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
    assert prepared["body_html"].startswith(
        "<p>Prezado(a) Senhor(a),</p>"
        "<p>Em anexo, encaminhamos a folha de serviço relativa à intervenção "
        "<strong>FS-123</strong>.</p>"
        "<p>Esta comunicação é enviada para um único endereço de e-mail. "
        "Caso considere necessário, agradecemos o seu reencaminhamento interno.</p>"
        "<p>Permanecemos à disposição para qualquer esclarecimento.</p>"
    )


def simulated_transport(*args, **kwargs):
    # Each transport test replaces urlopen; the suite also blocks real sockets.
    return urlrequest.urlopen(*args, **kwargs)
    assert prepared["body_html"].count('src="cid:assinatura-dora@sensorpoint.pt"') == 1


def test_connection_validates_mail_send_permission_without_sending(monkeypatch):
    claims = base64.urlsafe_b64encode(
        json.dumps(
            {
                "roles": ["Mail.Send", "Sites.ReadWrite.All"],
                "azp": "client",
                "tid": "tenant",
            }
        ).encode("utf-8")
    ).decode("ascii").rstrip("=")
    token = f"header.{claims}.signature"
    requested_urls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({"access_token": token, "expires_in": 3600}).encode("utf-8")

    def fake_urlopen(req, timeout):
        requested_urls.append(req.full_url)
        return FakeResponse()

    monkeypatch.setattr("src.services.graph_mail_service.request.urlopen", fake_urlopen)
    service = GraphMailService(mail_config(), transport=simulated_transport)

    result = service.test_connection()

    assert result == {
        "authenticated": True,
        "mail_send_permission": True,
        "sender": "service@sensorpoint.pt",
        "application_id": "client",
        "tenant_id": "tenant",
    }
    assert len(requested_urls) == 1
    assert requested_urls[0].startswith("https://login.microsoftonline.com/")
    assert all("sendMail" not in url for url in requested_urls)


def test_connection_reports_an_invalid_secret_as_configuration_error(monkeypatch):
    def rejected_urlopen(req, timeout):
        raise urlerror.HTTPError(
            req.full_url,
            400,
            "Bad Request",
            {},
            io.BytesIO(
                json.dumps(
                    {
                        "error": "invalid_client",
                        "error_description": "AADSTS7000215: Invalid client secret provided.",
                    }
                ).encode("utf-8")
            ),
        )

    monkeypatch.setattr("src.services.graph_mail_service.request.urlopen", rejected_urlopen)
    service = GraphMailService(mail_config(), transport=simulated_transport)

    with pytest.raises(GraphMailConfigurationError, match="secret"):
        service.test_connection()


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
    stale_prepared = {
        "to": "cliente-real@example.com",
        "cc": ["jribeiro@sensorpoint.pt", "dsilva@sensorpoint.pt"],
        "subject": "Folha de serviço",
        "body_html": "<p>Teste</p>",
    }
    result = service.send_prepared(
        stale_prepared,
        pdf_path,
        operation_id="send:stale:test",
    )

    encoded = json.dumps(captured["payload"])
    assert result["to"] == "jribeiro@sensorpoint.pt"
    assert "cliente-real@example.com" not in encoded
    assert captured["payload"]["message"]["subject"].startswith("[TESTE]")
    assert result["cc"] == []
    body = captured["payload"]["message"]["body"]["content"]
    assert body.startswith("<p>Teste</p>")
    assert body.count('src="cid:assinatura-dora@sensorpoint.pt"') == 1
    assert captured["payload"]["message"]["attachments"][1]["isInline"] is True

    service.send_prepared(stale_prepared, pdf_path, operation_id="send:stale:test")
    assert captured["payload"]["message"]["body"]["content"] == body
    assert stale_prepared["body_html"] == "<p>Teste</p>"


def test_prepare_service_email_rejects_invalid_customer_address():
    service = GraphMailService(mail_config(), transport=simulated_transport)

    with pytest.raises(GraphMailError, match="cliente"):
        service.prepare_service_email(
            customer_email="endereco-invalido",
            technician_email="tecnico@sensorpoint.pt",
            customer_name="Cliente",
            service_number="FS-123",
        )


@pytest.mark.parametrize("language", ["pt", "en"])
def test_send_prepared_posts_pdf_and_inline_signature_to_graph(tmp_path, monkeypatch, language):
    service = GraphMailService(mail_config(), transport=simulated_transport)
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
        document_language=language,
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
    assert len(message["attachments"]) == 2
    pdf, signature = message["attachments"]
    assert pdf["name"] == "FS-123__folha_final.pdf"
    assert pdf["contentType"] == "application/pdf"
    assert not pdf.get("isInline", False)
    assert base64.b64decode(pdf["contentBytes"]) == pdf_path.read_bytes()
    assert signature["name"] == "assinatura-dora.jpg"
    assert signature["@odata.type"] == "#microsoft.graph.fileAttachment"
    assert signature["contentType"] == "image/jpeg"
    assert signature["isInline"] is True
    assert base64.b64decode(signature["contentBytes"]) == MAIL_SIGNATURE_PATH.read_bytes()
    assert message["body"]["contentType"] == "HTML"
    assert message["body"]["content"] == prepared["body_html"]
    assert message["body"]["content"].count(f'src="cid:{signature["contentId"]}"') == 1
    assert 'width="792" height="230"' in message["body"]["content"]
    assert "max-width:100%;height:auto;" in message["body"]["content"]
    assert message["internetMessageHeaders"][0]["value"] == "send:documento:1"


@pytest.mark.parametrize("stage", ["prepare", "send"])
@pytest.mark.parametrize("signature_bytes", [None, b"not-a-jpeg"])
def test_mail_rejects_missing_or_invalid_signature_before_graph(
    tmp_path, monkeypatch, stage, signature_bytes,
):
    service = GraphMailService(mail_config(), transport=simulated_transport)
    fields = {
        "customer_email": "cliente@example.com",
        "technician_email": "tecnico@sensorpoint.pt",
        "customer_name": "Cliente",
        "service_number": "FS-123",
    }
    prepared = service.prepare_service_email(**fields) if stage == "send" else None
    pdf_path = tmp_path / "FS-123.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\nconteudo")
    signature_path = tmp_path / "signature.jpg"
    if signature_bytes is not None:
        signature_path.write_bytes(signature_bytes)
    monkeypatch.setattr("src.services.graph_mail_service.MAIL_SIGNATURE_PATH", signature_path)

    def unexpected_graph_request(*_args, **_kwargs):
        pytest.fail("Uma assinatura inválida não deve permitir um pedido ao Graph.")

    monkeypatch.setattr(service, "_graph_bytes", unexpected_graph_request)
    with pytest.raises(GraphMailConfigurationError, match="assinatura"):
        if stage == "prepare":
            service.prepare_service_email(**fields)
        else:
            service.send_prepared(prepared, pdf_path, operation_id="send:missing-signature")


def test_send_prepared_counts_inline_signature_in_request_size_limit(tmp_path, monkeypatch):
    service = GraphMailService(mail_config(), transport=simulated_transport)
    pdf_path = tmp_path / "FS-LARGE.pdf"
    # O PDF isolado cabe no limite de 3 MB; o pedido com a assinatura já não cabe.
    pdf_path.write_bytes(b"%PDF-1.7\n" + b" " * (3 * 1024 * 1024 - 32))
    prepared = service.prepare_service_email(
        customer_email="cliente@example.com",
        technician_email="tecnico@sensorpoint.pt",
        customer_name="Cliente",
        service_number="FS-LARGE",
    )

    def unexpected_graph_request(*_args, **_kwargs):
        pytest.fail("Um pedido demasiado grande não deve ser enviado ao Graph.")

    monkeypatch.setattr(service, "_graph_bytes", unexpected_graph_request)
    with pytest.raises(GraphMailError, match="PDF e a assinatura excede 4 MB"):
        service.send_prepared(prepared, pdf_path, operation_id="send:oversized-signature")


def test_graph_429_is_retryable_and_respects_retry_after(tmp_path, monkeypatch):
    service = GraphMailService(mail_config(), transport=simulated_transport)
    pdf_path = tmp_path / "FS-503__folha_final.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\nconteudo")
    prepared = service.prepare_service_email(
        customer_email="cliente@example.com",
        technician_email="tecnico@sensorpoint.pt",
        customer_name="Cliente",
        service_number="FS-503",
    )
    calls = 0

    class TokenResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({"access_token": "token", "expires_in": 3600}).encode("utf-8")

    def fake_urlopen(req, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            return TokenResponse()
        raise urlerror.HTTPError(
            req.full_url,
            429,
            "Service Unavailable",
            {"Retry-After": "7"},
            io.BytesIO(b'{"error":{"code":"serviceNotAvailable"}}'),
        )

    monkeypatch.setattr("src.services.graph_mail_service.request.urlopen", fake_urlopen)

    with pytest.raises(GraphMailTransientError) as raised:
        service.send_prepared(prepared, pdf_path, operation_id="send:503")

    assert raised.value.retry_after_seconds == 7
    assert calls == 2


def test_graph_network_outage_requires_reconciliation(tmp_path, monkeypatch):
    service = GraphMailService(mail_config(), transport=simulated_transport)
    pdf_path = tmp_path / "FS-NET__folha_final.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\nconteudo")
    prepared = service.prepare_service_email(
        customer_email="cliente@example.com",
        technician_email="tecnico@sensorpoint.pt",
        customer_name="Cliente",
        service_number="FS-NET",
    )
    service._access_token = "cached-token"
    service._access_token_expires_at = time.time() + 300

    def unavailable_urlopen(req, timeout):
        raise urlerror.URLError("sem ligação")

    monkeypatch.setattr("src.services.graph_mail_service.request.urlopen", unavailable_urlopen)

    with pytest.raises(GraphMailUnknownResultError, match="desconhecido"):
        service.send_prepared(prepared, pdf_path, operation_id="send:network")


def test_send_prepared_rejects_an_excel_attachment_even_with_pdf_content(tmp_path):
    service = GraphMailService(mail_config(), transport=simulated_transport)
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


def test_local_pdf_renders_in_configured_local_temp_before_publishing(tmp_path, monkeypatch):
    archive_dir = tmp_path / "OneDrive" / "Arquivadas" / "FS-LOCAL"
    archive_dir.mkdir(parents=True)
    excel_path = archive_dir / "FS-LOCAL.xlsx"
    excel_path.write_bytes(b"excel")
    html_path = archive_dir / "FS-LOCAL__folha_final.html"
    html_path.write_text("<html><body>Folha final</body></html>", encoding="utf-8")
    local_temp = tmp_path / "local-pdf-temp"
    monkeypatch.setenv("FS_PDF_TEMP_DIR", str(local_temp))
    rendered_paths = []

    def fake_renderer(source, destination):
        assert source == html_path
        rendered_paths.append(destination)
        destination.write_bytes(b"%PDF-1.7\nlocal")

    pdf_path = LocalPdfService(fake_renderer).export_archive_pdf(excel_path)

    assert rendered_paths[0].parent == local_temp.resolve()
    assert rendered_paths[0].exists() is False
    assert pdf_path == archive_dir / "FS-LOCAL__folha_final.pdf"
    assert pdf_path.read_bytes().startswith(b"%PDF-")
    assert list(archive_dir.glob(".*.tmp.pdf")) == []


def test_local_pdf_browser_avoids_child_process_pipes_and_closes_job(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("FS_PDF_BROWSER_NO_SANDBOX", raising=False)
    browser = tmp_path / "chrome.exe"
    browser.write_bytes(b"browser")
    html_path = tmp_path / "folha.html"
    html_path.write_text("<html><body>Folha</body></html>", encoding="utf-8")
    destination = tmp_path / "folha.pdf"
    captured = {}
    job = object()
    closed_jobs = []

    class CompletedProcess:
        pid = 123
        _handle = 456

        @staticmethod
        def wait(timeout):
            captured["wait_timeout"] = timeout
            return 0

        @staticmethod
        def poll():
            return 0

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        destination.write_bytes(b"%PDF-1.7\nprocesso")
        return CompletedProcess()

    monkeypatch.setattr(LocalPdfService, "_find_browser", staticmethod(lambda: browser))
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        LocalPdfService,
        "_create_windows_process_job",
        staticmethod(lambda _process: job),
    )
    monkeypatch.setattr(
        LocalPdfService,
        "_close_windows_process_job",
        staticmethod(lambda handle: closed_jobs.append(handle) if handle else None),
    )

    LocalPdfService._render_with_browser(html_path, destination)

    assert captured["wait_timeout"] == 60
    assert captured["kwargs"]["stdout"] is not subprocess.PIPE
    assert captured["kwargs"]["stderr"] == subprocess.STDOUT
    assert "--disable-breakpad" not in captured["command"]
    assert "--disable-dev-shm-usage" in captured["command"]
    assert "--no-sandbox" not in captured["command"]
    assert closed_jobs == [job]


def test_local_pdf_browser_timeout_terminates_the_process_tree(tmp_path, monkeypatch):
    browser = tmp_path / "chrome.exe"
    browser.write_bytes(b"browser")
    html_path = tmp_path / "folha.html"
    html_path.write_text("<html><body>Folha</body></html>", encoding="utf-8")
    destination = tmp_path / "folha.pdf"
    job = object()
    terminated = []

    class TimedOutProcess:
        pid = 789
        _handle = 987
        stopped = False

        def wait(self, timeout):
            if not self.stopped:
                raise subprocess.TimeoutExpired("chrome", timeout)
            return -1

        def poll(self):
            return -1 if self.stopped else None

    process = TimedOutProcess()

    def fake_terminate(received_process, received_job):
        terminated.append((received_process, received_job))
        received_process.stopped = True
        return None

    monkeypatch.setattr(LocalPdfService, "_find_browser", staticmethod(lambda: browser))
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        LocalPdfService,
        "_create_windows_process_job",
        staticmethod(lambda _process: job),
    )
    monkeypatch.setattr(
        LocalPdfService,
        "_terminate_browser_process",
        staticmethod(fake_terminate),
    )
    monkeypatch.setattr(
        LocalPdfService,
        "_close_windows_process_job",
        staticmethod(lambda _handle: None),
    )

    with pytest.raises(LocalPdfError, match="excedeu 60 segundos"):
        LocalPdfService._render_with_browser(html_path, destination)

    assert terminated == [(process, job)]


def test_graph_storage_converts_final_html_and_uploads_pdf(tmp_path, monkeypatch):
    archive_dir = tmp_path / "Arquivadas" / "FS-123"
    archive_dir.mkdir(parents=True)
    archived_excel = archive_dir / "FS-123.xlsx"
    archived_excel.write_bytes(b"excel")
    final_html = archive_dir / "FS-123__folha_final.html"
    final_html.write_text("<html><body>Folha final</body></html>", encoding="utf-8")
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
        return b"%PDF-1.7\nhtml-convertido"

    def fake_upload_file(local_path, remote_path, *, expected_etag=None):
        calls["upload"] = (Path(local_path), remote_path, expected_etag)
        return {"id": "pdf-id", "name": Path(local_path).name, "eTag": "etag-pdf"}

    monkeypatch.setattr(service, "_graph_bytes", fake_graph_bytes)
    monkeypatch.setattr(service, "upload_file", fake_upload_file)
    monkeypatch.setattr(service, "_write_item_meta", lambda *_args: None)

    pdf_path, remote_pdf = service.export_archive_pdf(archived_excel)

    assert pdf_path.read_bytes() == b"%PDF-1.7\nhtml-convertido"
    assert calls["convert"] == (
        "GET",
        "/drives/drive/root:/Aplicacao/Arquivadas/FS-123/"
        "FS-123__folha_final.html:/content?format=pdf",
    )
    assert remote_pdf == "Aplicacao/Arquivadas/FS-123/FS-123__folha_final.pdf"
    assert calls["upload"][0] == pdf_path


def test_archive_queue_converts_remote_html_and_sends_same_pdf_once(tmp_path):
    archive_dir = tmp_path / "Arquivadas" / "FS-123"
    archive_dir.mkdir(parents=True)
    archived_excel = archive_dir / "FS-123.xlsx"
    archived_excel.write_bytes(b"excel")
    final_html = archive_dir / "FS-123__folha_final.html"
    final_html.write_text("<html><body>Folha final</body></html>", encoding="utf-8")
    events = []

    class FakeGraph:
        def upload_archive_bundle(self, path):
            assert Path(path).with_name("FS-123__folha_final.html") == final_html
            events.append(("upload", Path(path)))
            return [
                "Arquivadas/FS-123/FS-123.xlsx",
                "Arquivadas/FS-123/FS-123__folha_final.html",
            ]

        def export_archive_pdf(self, path):
            assert Path(path).with_name("FS-123__folha_final.html") == final_html
            events.append(("graph_html_pdf", final_html))
            pdf = Path(path).with_name("FS-123__folha_final.pdf")
            pdf.write_bytes(b"%PDF-1.7\nqueue-html")
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
    assert [event[0] for event in events] == ["upload", "graph_html_pdf", "remove", "mail"]
    assert events[-1][3] == "send:FS-123:stable"
    assert events[-1][2].read_bytes() == b"%PDF-1.7\nqueue-html"


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


def test_stale_running_mail_job_is_held_for_manual_retry(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("FS_MAIL_JOB_STALE_SECONDS", "90")
    monkeypatch.setattr(GraphSyncQueue, "start", lambda _self: None)

    class UnexpectedMail:
        calls = 0

        def send_prepared(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("Um trabalho stale não pode enviar automaticamente.")

    mail = UnexpectedMail()
    database = tmp_path / "stale-mail.sqlite3"
    queue = GraphSyncQueue(None, database, mail_service=mail)
    queued = queue.enqueue(
        "archive_and_mail_local",
        {
            "archived_path": str(tmp_path / "FS-STALE.xlsx"),
            "mail": {"to": "cliente@example.com"},
        },
        job_id="send:stale:manual",
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE graph_sync_jobs SET status='running', updated_at=? WHERE id=?",
            (time.time() - 91, queued["id"]),
        )

    held = queue.status(queued["id"])

    assert held["status"] == "failed"
    assert held["will_retry"] is False
    assert "repetição manual" in held["last_error"]
    assert mail.calls == 0


def test_stale_mail_job_with_accepted_checkpoint_is_completed(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("FS_MAIL_JOB_STALE_SECONDS", "90")
    monkeypatch.setattr(GraphSyncQueue, "start", lambda _self: None)
    database = tmp_path / "accepted-stale-mail.sqlite3"
    queue = GraphSyncQueue(None, database, mail_service=object())
    queued = queue.enqueue(
        "archive_and_mail_local",
        {
            "archived_path": str(tmp_path / "FS-ACCEPTED.xlsx"),
            "mail": {"to": "cliente@example.com"},
        },
        job_id="send:stale:accepted",
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            UPDATE graph_sync_jobs
            SET status='running', updated_at=?, result_json=?
            WHERE id=?
            """,
            (
                time.time() - 91,
                json.dumps({"phase": "mail_accepted", "mail": {"accepted": True}}),
                queued["id"],
            ),
        )

    reconciled = queue.status(queued["id"])

    assert reconciled["status"] == "complete"
    assert reconciled["result"]["mail"]["accepted"] is True


def test_failure_to_write_retry_state_falls_back_to_terminal_hold(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(GraphSyncQueue, "start", lambda _self: None)
    database = tmp_path / "state-fallback.sqlite3"
    queue = GraphSyncQueue(None, database, mail_service=object())
    queued = queue.enqueue(
        "archive_and_mail_local",
        {
            "archived_path": str(tmp_path / "FS-STATE.xlsx"),
            "mail": {"to": "cliente@example.com"},
        },
        job_id="send:state:fallback",
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE graph_sync_jobs SET status='running' WHERE id=?",
            (queued["id"],),
        )
    job = queue.status(queued["id"])
    monkeypatch.setattr(
        queue,
        "_mark_failed",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            sqlite3.OperationalError("database is locked")
        ),
    )

    queue._record_job_failure(job, RuntimeError("falha original"))
    held = queue.status(queued["id"])

    assert held["status"] == "failed"
    assert held["will_retry"] is False
    assert "Falha interna" in held["last_error"]


def test_permanent_mail_failure_is_not_retried_automatically(tmp_path):
    archive_dir = tmp_path / "Arquivadas" / "FS-AUTH"
    archive_dir.mkdir(parents=True)
    archived_excel = archive_dir / "FS-AUTH.xlsx"
    archived_excel.write_bytes(b"excel")
    calls = []

    class FakeLocalPdf:
        def export_archive_pdf(self, path):
            pdf = Path(path).with_name("FS-AUTH__folha_final.pdf")
            pdf.write_bytes(b"%PDF-1.7\nlocal")
            return pdf

    class RejectedMail:
        def send_prepared(self, prepared, attachment_path, *, operation_id):
            calls.append(operation_id)
            raise GraphMailError("A autenticação do serviço de e-mail foi rejeitada.")

    queue = GraphSyncQueue(
        None,
        tmp_path / "permanent-mail.sqlite3",
        mail_service=RejectedMail(),
        local_pdf_service=FakeLocalPdf(),
    )
    queued = queue.enqueue(
        "archive_and_mail_local",
        {
            "archived_path": str(archived_excel),
            "mail": {"to": "cliente@example.com", "technician_email": "tecnico@example.com"},
        },
        job_id="send:auth:permanent",
    )

    failed = queue.wait(queued["id"], timeout=3)

    assert failed["status"] == "failed"
    assert failed["will_retry"] is False
    assert calls == ["send:auth:permanent"]


def test_transient_mail_failure_stops_after_configured_attempt_limit(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("FS_MAIL_JOB_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("FS_GRAPH_JOB_RETRY_BASE_SECONDS", "0.01")
    archive_dir = tmp_path / "Arquivadas" / "FS-TRANSIENT"
    archive_dir.mkdir(parents=True)
    archived_excel = archive_dir / "FS-TRANSIENT.xlsx"
    archived_excel.write_bytes(b"excel")
    calls = []

    class FakeLocalPdf:
        def export_archive_pdf(self, path):
            pdf = Path(path).with_name("FS-TRANSIENT__folha_final.pdf")
            pdf.write_bytes(b"%PDF-1.7\nlocal")
            return pdf

    class UnavailableMail:
        def send_prepared(self, prepared, attachment_path, *, operation_id):
            calls.append(operation_id)
            raise GraphMailTransientError("O serviço de e-mail está temporariamente indisponível.")

    queue = GraphSyncQueue(
        None,
        tmp_path / "transient-mail.sqlite3",
        mail_service=UnavailableMail(),
        local_pdf_service=FakeLocalPdf(),
    )
    queued = queue.enqueue(
        "archive_and_mail_local",
        {
            "archived_path": str(archived_excel),
            "mail": {"to": "cliente@example.com", "technician_email": "tecnico@example.com"},
        },
        job_id="send:transient:limited",
    )

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        failed = queue.status(queued["id"])
        if failed["status"] == "failed" and not failed["will_retry"]:
            break
        time.sleep(0.01)

    assert failed["status"] == "failed"
    assert failed["will_retry"] is False
    assert failed["attempts"] == 2
    assert failed["max_attempts"] == 2
    assert calls == ["send:transient:limited", "send:transient:limited"]


def test_terminal_mail_failure_only_runs_again_after_explicit_retry(tmp_path):
    archive_dir = tmp_path / "Arquivadas" / "FS-MANUAL"
    archive_dir.mkdir(parents=True)
    archived_excel = archive_dir / "FS-MANUAL.xlsx"
    archived_excel.write_bytes(b"excel")

    class FakeLocalPdf:
        def export_archive_pdf(self, path):
            pdf = Path(path).with_name("FS-MANUAL__folha_final.pdf")
            pdf.write_bytes(b"%PDF-1.7\nlocal")
            return pdf

    class RecoveringMail:
        available = False
        calls = 0

        def send_prepared(self, prepared, attachment_path, *, operation_id):
            self.calls += 1
            if not self.available:
                raise GraphMailError("A autenticação do serviço de e-mail foi rejeitada.")
            return {"accepted": True}

    mail = RecoveringMail()
    queue = GraphSyncQueue(
        None,
        tmp_path / "manual-retry.sqlite3",
        mail_service=mail,
        local_pdf_service=FakeLocalPdf(),
    )
    queued = queue.enqueue(
        "archive_and_mail_local",
        {
            "archived_path": str(archived_excel),
            "mail": {"to": "cliente@example.com", "technician_email": "tecnico@example.com"},
        },
        job_id="send:manual:retry",
    )
    failed = queue.wait(queued["id"], timeout=3)
    assert failed["status"] == "failed"
    assert mail.calls == 1

    mail.available = True
    queue.retry(queued["id"])
    completed = queue.wait(queued["id"], timeout=3)

    assert completed["status"] == "complete"
    assert completed["result"]["mail"]["accepted"] is True
    assert mail.calls == 2


def test_legacy_pending_mail_is_held_for_explicit_review_on_upgrade(tmp_path):
    archive_dir = tmp_path / "Arquivadas" / "FS-LEGACY"
    archive_dir.mkdir(parents=True)
    archived_excel = archive_dir / "FS-LEGACY.xlsx"
    archived_excel.write_bytes(b"excel")
    database = tmp_path / "legacy.sqlite3"
    now = time.time()
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE graph_sync_jobs (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at REAL NOT NULL,
                last_error TEXT,
                result_json TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO graph_sync_jobs (
                id, kind, payload_json, status, attempts, next_attempt_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, 'pending', 0, ?, ?, ?)
            """,
            (
                "send:legacy:pending",
                "archive_and_mail_local",
                json.dumps(
                    {
                        "archived_path": str(archived_excel),
                        "mail": {"to": "cliente@example.com"},
                    }
                ),
                now,
                now,
                now,
            ),
        )

    class UnexpectedMail:
        calls = 0

        def send_prepared(self, prepared, attachment_path, *, operation_id):
            self.calls += 1
            return {"accepted": True}

    mail = UnexpectedMail()
    queue = GraphSyncQueue(
        None,
        database,
        mail_service=mail,
        local_pdf_service=object(),
    )
    time.sleep(0.1)

    held = queue.status("send:legacy:pending")

    assert held["status"] == "failed"
    assert held["will_retry"] is False
    assert "repetição manual" in held["last_error"]
    assert mail.calls == 0
