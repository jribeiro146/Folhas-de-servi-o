"""Optional institutional image stays private and belongs to the fixed message."""
import base64
import json
from dataclasses import replace
from pathlib import Path

from PIL import Image
import pytest

import src.services.graph_mail_service as mail
from src.services.graph_sync_queue import GraphSyncQueue
from tests.test_durable_mail import Graph, make_mail


def setup_signature(tmp_path, monkeypatch, *, filename="institution.png", image_format="PNG"):
    monkeypatch.setattr(mail, "APP_DATA_DIR", tmp_path)
    private = tmp_path / "mail-assets"
    private.mkdir()
    image_path = private / filename
    Image.new("RGB", (320, 80), "white").save(image_path, format=image_format)
    pdf = tmp_path / "sheet.pdf"
    pdf.write_bytes(b"%PDF-synthetic")
    calls = []
    service = make_mail(calls)
    service.config = replace(service.config, signature_filename=filename)
    return service, image_path, pdf, calls


@pytest.mark.parametrize("filename,image_format", [("institution.png", "PNG"), ("institution.jpg", "JPEG")])
def test_signature_is_inline_and_fixed_after_private_files_are_removed(tmp_path, monkeypatch, filename, image_format):
    service, signature, pdf, calls = setup_signature(tmp_path, monkeypatch, filename=filename, image_format=image_format)
    original = signature.read_bytes()
    delivery = service.prepare_delivery({"to": "customer@example.invalid", "body_html": "<p>Original</p>"},
                                        pdf, operation_id="signature:synthetic")
    assert calls == []
    message = json.loads(delivery.payload)["message"]
    assert len(message["attachments"]) == 2
    document, image = message["attachments"]
    assert base64.b64decode(document["contentBytes"]) == b"%PDF-synthetic"
    assert image["isInline"] is True and image["contentType"] == ("image/png" if image_format == "PNG" else "image/jpeg")
    assert base64.b64decode(image["contentBytes"]) == original
    assert message["body"]["content"].startswith("<p>Original</p>")
    assert message["body"]["content"].count("cid:" + image["contentId"]) == 1
    assert str(tmp_path) not in delivery.payload.decode()
    signature.unlink()
    pdf.unlink()
    service.config = replace(service.config, signature_filename="")
    assert service.send_delivery(delivery)["accepted"] is True
    sends = [r for r in calls if r.full_url.endswith("/sendMail")]
    assert len(sends) == 1 and sends[0].data == delivery.payload


@pytest.mark.parametrize("filename", ["../outside.png", "sub/image.png", r"sub\image.png", "C:/image.png", "image.png:stream", "."])
def test_only_a_private_basename_can_be_configured(tmp_path, monkeypatch, filename):
    service, _, pdf, calls = setup_signature(tmp_path, monkeypatch)
    service.config = replace(service.config, signature_filename=filename)
    with pytest.raises(mail.GraphMailConfigurationError):
        service.prepare_delivery({"to": "customer@example.invalid"}, pdf, operation_id="invalid")
    assert calls == []


@pytest.mark.parametrize("damage", ["missing", "unreadable", "fake_image", "large_bytes", "large_dimensions", "wrong_extension", "directory", "truncated_jpeg"])
def test_configured_invalid_signature_preserves_active_sheet(tmp_path, monkeypatch, damage):
    service, signature, _, calls = setup_signature(tmp_path, monkeypatch)
    if damage == "missing":
        signature.unlink()
    elif damage == "unreadable":
        original_open = Path.open
        def denied(path, *a, **kw):
            if path == signature:
                raise PermissionError("Synthetic unreadable signature")
            return original_open(path, *a, **kw)
        monkeypatch.setattr(Path, "open", denied)
    elif damage == "fake_image":
        signature.write_bytes(b"\x89PNG\r\n\x1a\ninvalid")
    elif damage == "large_bytes":
        signature.write_bytes(b"x" * (512 * 1024 + 1))
    elif damage == "large_dimensions":
        Image.new("RGB", (2049, 20), "white").save(signature)
    elif damage == "wrong_extension":
        Image.new("RGB", (10, 10), "white").save(signature, format="JPEG")
    elif damage == "truncated_jpeg":
        signature.unlink()
        signature = signature.with_suffix(".jpg")
        service.config = replace(service.config, signature_filename=signature.name)
        Image.new("RGB", (320, 80), "white").save(signature)
        signature.write_bytes(signature.read_bytes()[:-20])
    else:
        signature.unlink()
        signature.mkdir()
    queue = GraphSyncQueue(Graph(tmp_path), tmp_path / "queue.sqlite3", mail_service=service, auto_start=False)
    job = queue.enqueue("archive_and_remove", {"archived_path": str(tmp_path / "sheet.xlsx"),
        "source_name": "sheet.xlsx", "mail": {"to": "customer@example.invalid"}})
    queue.run_until_idle()
    state = queue.status(job["id"])
    assert state["status"] == "failed" and state["result"]["phase"] == "mail_rejected"
    assert not state["will_retry"] and not state["reconciliation_required"]
    assert not (tmp_path / "remote-removed").exists() and calls == []


def test_signature_counts_towards_total_encoded_limit(tmp_path, monkeypatch):
    service, _, pdf, calls = setup_signature(tmp_path, monkeypatch)
    prepared = {"to": "customer@example.invalid", "body_html": ""}
    unsigned = mail.GraphMailService(replace(service.config, signature_filename=""), transport=service._transport)
    empty = unsigned.prepare_delivery(prepared, pdf, operation_id="size")
    prepared["body_html"] = "x" * (mail.MAX_SIMPLE_REQUEST_BYTES - len(empty.payload))
    assert len(unsigned.prepare_delivery(prepared, pdf, operation_id="size").payload) == mail.MAX_SIMPLE_REQUEST_BYTES
    with pytest.raises(mail.GraphMailError):
        service.prepare_delivery(prepared, pdf, operation_id="size")
    assert calls == []


@pytest.mark.parametrize("redirect", ["root", "file"])
def test_private_signature_rejects_links_resolving_elsewhere(tmp_path, monkeypatch, redirect):
    service, signature, pdf, calls = setup_signature(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / signature.name).write_bytes(signature.read_bytes())
    original_resolve = Path.resolve
    target = signature.parent if redirect == "root" else signature
    def resolved(path, *a, **kw):
        if path == target:
            return outside if redirect == "root" else outside / signature.name
        return original_resolve(path, *a, **kw)
    monkeypatch.setattr(Path, "resolve", resolved)
    with pytest.raises(mail.GraphMailConfigurationError):
        service.prepare_delivery({"to": "customer@example.invalid"}, pdf, operation_id="linked")
    assert calls == []


def test_private_signature_cannot_be_read_from_public_application_resources(tmp_path, monkeypatch):
    service, _, pdf, calls = setup_signature(tmp_path, monkeypatch)
    # Synthetic source root enclosing a valid image, not a missing-path refusal.
    monkeypatch.setattr(mail, "__file__", str(tmp_path / "services" / "graph_mail_service.py"))
    with pytest.raises(mail.GraphMailConfigurationError):
        service.prepare_delivery({"to": "customer@example.invalid"}, pdf, operation_id="public")
    assert calls == []


def test_signature_file_read_is_bounded(tmp_path, monkeypatch):
    service, signature, pdf, calls = setup_signature(tmp_path, monkeypatch)
    original_open = Path.open
    sizes = []
    class Bounded:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.stream.close()
        def read(self, size=-1):
            assert size == 512 * 1024 + 1
            sizes.append(size)
            return self.stream.read(size)
    monkeypatch.setattr(Path, "open", lambda path, *a, **kw:
                        Bounded(original_open(path, *a, **kw)) if path == signature else original_open(path, *a, **kw))
    service.prepare_delivery({"to": "customer@example.invalid"}, pdf, operation_id="bounded")
    assert sizes == [512 * 1024 + 1] and calls == []


@pytest.mark.parametrize("damage", ["second_pdf", "not_inline", "bad_cid", "bad_image", "missing_cid", "third_attachment"])
def test_fixed_signature_validation_rejects_changed_envelope(tmp_path, monkeypatch, damage):
    service, _, pdf, calls = setup_signature(tmp_path, monkeypatch)
    fixed = service.prepare_delivery({"to": "customer@example.invalid"}, pdf, operation_id="signature")
    payload = json.loads(fixed.payload)
    message = payload["message"]
    signature = message["attachments"][1]
    if damage == "second_pdf":
        message["attachments"][1] = message["attachments"][0]
    elif damage == "not_inline":
        signature["isInline"] = False
    elif damage == "bad_cid":
        signature["contentId"] = "another"
    elif damage == "bad_image":
        signature["contentBytes"] = base64.b64encode(b"fake").decode()
    elif damage == "missing_cid":
        message["body"]["content"] = "<p>No image</p>"
    else:
        message["attachments"].append(signature)
    with pytest.raises(mail.GraphMailConfigurationError):
        service.send_delivery(replace(fixed, payload=json.dumps(payload).encode()))
    assert calls == []
