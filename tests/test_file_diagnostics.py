"""Synthetic diagnostics: no live Graph, mail, queue or operational files."""

import json
import logging
import tempfile
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from src.logging_config import JsonLogFormatter
from src.services.file_diagnostics import SYNC_ID, file_fields, lookup_fields
from src.services.file_service import FileService
from src.services.graph_storage_service import GraphConfig, GraphStorageService


@pytest.fixture
def local_dir():
    # Use a unique synthetic directory with inherited Windows ACLs.
    directory = Path(tempfile.gettempdir()) / ("fs-log-diagnostics-" + uuid.uuid4().hex)
    directory.mkdir()
    return directory


def events(caplog, event):
    return [r.event_fields for r in caplog.records if getattr(r, "event", None) == event]


@pytest.mark.parametrize("failure, expected_code", [("metadata", "editing_session_required"), ("document", "document_invalid"), ("attachments", "attachments_invalid")])
def test_rejected_requests_record_sheet_and_category_without_private_content(local_dir, monkeypatch, caplog, failure, expected_code):
    import src.web.application as web
    from src.services.excel_service import ExcelValidationError
    from src.services.photo_attachment_service import PhotoAttachmentError

    monkeypatch.setattr(web, "LOG_REQUESTS", True)
    caplog.set_level(logging.INFO)
    private_detail = "synthetic-secret-content <email-private>"
    if failure != "metadata":
        error = ExcelValidationError if failure == "document" else PhotoAttachmentError

        def reject(_files):
            raise error(private_detail)

        monkeypatch.setattr(web.PhotoAttachmentService, "prepare_uploads", reject)
    name = "2026_9991 Cliente Confidencial"
    (local_dir / f"{name}.xlsx").write_bytes(b"synthetic")
    app = web.create_app(file_service=FileService(local_dir, require_remote_index=False))
    endpoint = "send" if failure == "metadata" else "draft"
    response = app.test_client().post(f"/api/file/{name}/{endpoint}", json={"customer_name":private_detail})
    assert response.status_code == (409 if failure == "metadata" else 400)
    record = events(caplog, "http_request_completed")[-1]
    assert record["error_code"] == expected_code
    assert record["sheet_number"] == "2026_9991"
    assert record["file_ref"] == file_fields(name)["file_ref"]
    output = "\n".join(JsonLogFormatter().format(r) for r in caplog.records)
    assert private_detail not in output
    assert "Cliente Confidencial" not in output
    assert str(local_dir) not in output


def test_send_validation_records_counts_without_logging_field_values(local_dir, monkeypatch, caplog):
    import shutil
    import src.web.application as web

    monkeypatch.setattr(web, "LOG_REQUESTS", True)
    caplog.set_level(logging.INFO)
    name = "2026_9992_2026-10-07_QA"
    shutil.copy2(Path(__file__).parent / "fixtures" / "test_sample.xlsx", local_dir / f"{name}.xlsx")
    client = web.create_app(file_service=FileService(local_dir, require_remote_index=False)).test_client()
    editing = client.get(f"/api/file/{name}/bootstrap?client_id=synthetic-validation").get_json()["editing"]
    caplog.clear()
    response = client.post(f"/api/file/{name}/send", json={
        "customer_name": "Private synthetic customer",
        "_edit": {"document_id":editing["document_id"], "client_id":"synthetic-validation",
                  "lease_token":editing["lease"]["token"], "base_revision":editing["revision"],
                  "idempotency_key":"synthetic-validation"},
    })
    assert response.status_code == 400
    record = events(caplog, "http_request_completed")[-1]
    assert record["error_code"] == "missing_fields"
    assert record["missing_fields_count"] == len(response.get_json()["missing_fields"])
    assert record["sheet_number"] == "2026_9992"
    output = "\n".join(JsonLogFormatter().format(r) for r in caplog.records)
    assert "Private synthetic customer" not in output
    assert editing["lease"]["token"] not in output


def test_missing_and_alternate_extension_are_private(local_dir, caplog):
    from src.web.application import create_app

    caplog.set_level(logging.INFO)
    name = "2026_9901 Cliente Privado"
    (local_dir / f"{name}.xlsm").write_bytes(b"synthetic")
    app = create_app(file_service=FileService(local_dir, require_remote_index=False))
    response = app.test_client().get(f"/api/file/{name}/bootstrap?client_id=test")
    assert response.status_code == 404
    record = events(caplog, "editor_file_not_found")[-1]
    assert record["sheet_number"] == "2026_9901"
    assert record["lookup_reason"] == "alternate_extension"
    assert record["expected_exists"] is False
    assert record["local_candidates"][0]["extension"] == ".xlsm"
    output = "\n".join(JsonLogFormatter().format(r) for r in caplog.records)
    assert "Cliente Privado" not in output
    assert str(local_dir) not in output
    assert file_fields(name)["file_ref"] == file_fields(name + ".xlsm")["file_ref"]


def test_sync_inventory_and_cache_share_identifier(local_dir, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    service = GraphStorageService(GraphConfig("test", "test", "test", "test"))
    item = {"id": "private-item", "name": "2026_9902 Cliente Privado.xlsx", "lastModifiedDateTime": "2026-09-18T10:00:00Z",
            "eTag": "private-version", "size": 9, "file": {"mimeType": "application/vnd.ms-excel"}}
    monkeypatch.setattr(service, "_graph_json", lambda *a, **k: {"value": [item]})
    monkeypatch.setattr(service, "download_item", lambda _id, path: path.write_bytes(b"synthetic"))
    service.sync_active_files(local_dir)
    service.sync_active_files(local_dir)
    starts = events(caplog, "active_sync_started")
    assert len(starts) == 2 and starts[0]["sync_id"] != starts[1]["sync_id"]
    inventory = events(caplog, "graph_inventory_file")
    assert inventory[0]["sync_id"] == starts[0]["sync_id"]
    cache = events(caplog, "active_cache_file")
    assert [r["action"] for r in cache] == ["download_started", "download_completed", "reused"]
    assert cache[0]["file_ref"] == inventory[0]["file_ref"]
    assert SYNC_ID.get() is None
    output = "\n".join(JsonLogFormatter().format(r) for r in caplog.records)
    for private in ["Cliente Privado", "private-item", "private-version"]:
        assert private not in output


@pytest.mark.parametrize("kind", ["timeout", "http", "success"])
def test_http_outcomes_and_privacy(monkeypatch, caplog, kind):
    caplog.set_level(logging.DEBUG)
    from src.services.graph_storage_service import request

    request_id = "12345678-1234-1234-1234-123456789abc"

    class Response:
        status = 200
        headers = {"request-id": request_id}
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self): return b"ok"

    def open_fake(*args, **kwargs):
        assert kwargs["timeout"] == 60
        if kind == "timeout": raise TimeoutError("private detail")
        if kind == "http": raise HTTPError("https://private.invalid", 404, "private detail", {"request-id": request_id}, None)
        return Response()

    monkeypatch.setattr(request, "urlopen", open_fake)
    req = Request("https://private.invalid/customer?sig=secret", headers={"Authorization": "Bearer secret"})
    if kind == "success":
        assert GraphStorageService._open_request_with_retry(req, "GET") == b"ok"
    else:
        with pytest.raises((TimeoutError, HTTPError)):
            GraphStorageService._open_request_with_retry(req, "GET")
    record = events(caplog, "graph_http_attempt")[-1]
    assert record["status_code"] == {"timeout": None, "http": 404, "success": 200}[kind]
    assert record["graph_request_id"] == (None if kind == "timeout" else request_id)
    output = "\n".join(JsonLogFormatter().format(r) for r in caplog.records)
    assert "private" not in output and "secret" not in output


def test_failed_sync_resets_context(local_dir, monkeypatch, caplog):
    from src.services.file_mutex import FileMutexBusy
    caplog.set_level(logging.INFO)
    service = GraphStorageService(GraphConfig("test", "test", "test", "test"))
    def busy(*_): raise FileMutexBusy("busy")
    monkeypatch.setattr(service, "_sync_active_files_unlocked", busy)
    with pytest.raises(FileMutexBusy): service.sync_active_files(local_dir)
    assert events(caplog, "active_sync_failed")[-1]["reason"] == "already_running"
    assert SYNC_ID.get() is None


def test_removal_failure_reports_type_without_private_path(local_dir, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    path = local_dir / "2026_9903 Cliente Privado.xlsx"
    path.write_bytes(b"synthetic")
    def denied(*_): raise PermissionError("private path")
    monkeypatch.setattr(GraphStorageService, "_unlink_local_file", denied)
    GraphStorageService._remove_local_path(path)
    assert path.exists()
    record = events(caplog, "active_cache_removal")[-1]
    assert record["action"] == "failed" and record["error_type"] == "PermissionError"
    assert "private path" not in json.dumps(record)


def test_missing_then_loaded_uses_same_reference(local_dir, caplog):
    from openpyxl import Workbook
    from src.web.application import create_app
    caplog.set_level(logging.INFO)
    name = "2026_9904 Cliente Privado"
    client = create_app(file_service=FileService(local_dir, require_remote_index=False)).test_client()
    url = f"/api/file/{name}/bootstrap?client_id=synthetic-tab"
    assert client.get(url).status_code == 404
    book = Workbook()
    book.active.title = "LINK"
    book.create_sheet("FS")
    book.save(local_dir / f"{name}.xlsx")
    book.close()
    assert client.get(url).status_code == 200
    assert events(caplog, "editor_file_not_found")[-1]["file_ref"] == events(caplog, "editor_file_loaded")[-1]["file_ref"]


def test_diagnostic_failure_does_not_change_404(local_dir, monkeypatch, caplog):
    from src.web.application import create_app
    caplog.set_level(logging.INFO)
    service = FileService(local_dir, require_remote_index=False)
    def denied(*_): raise PermissionError("private path")
    monkeypatch.setattr(service, "_contained", denied)
    monkeypatch.setattr(service, "get_file_by_name", lambda _: None)
    response = create_app(file_service=service).test_client().get("/api/file/2026_9905/bootstrap")
    assert response.status_code == 404
    assert events(caplog, "editor_file_not_found")[-1]["diagnostic_error_type"] == "PermissionError"
