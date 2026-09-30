import io
import json
import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.services.archive_service import ArchiveService
from src.services.editing_state_service import EditingStateService
from src.services.file_service import FileService
from src.services.graph_storage_service import GraphConfig, GraphStorageService
from src.services.graph_sync_queue import GraphSyncQueue
from src.services.photo_attachment_service import (
    PHOTO_FOLDER_NAME,
    PhotoAttachmentError,
    PhotoAttachmentService,
)
from src.web.application import create_app


FIXTURE = Path(__file__).parent / "fixtures" / "test_sample.xlsx"
SIGNATURE_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+XgnsAAAAASUVORK5CYII="
)
JPEG = b"\xff\xd8\xff\xe0photo-jpeg"
PNG = b"\x89PNG\r\n\x1a\nphoto-png"


def upload(name: str, content: bytes):
    return SimpleNamespace(filename=name, stream=io.BytesIO(content))


@pytest.mark.parametrize(
    ("name", "content", "extension"),
    [
        ("camera.jpg", JPEG, "jpg"),
        ("camera.png", PNG, "png"),
        ("camera.webp", b"RIFF\x08\x00\x00\x00WEBPdata", "webp"),
        ("camera.gif", b"GIF89adata", "gif"),
        ("camera.bmp", b"BMbitmap", "bmp"),
        ("camera.tiff", b"II*\x00tiff", "tiff"),
        ("camera.dng", b"II*\x00dng", "dng"),
        ("camera.heic", b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1", "heic"),
        ("camera.avif", b"\x00\x00\x00\x18ftypavif\x00\x00\x00\x00mif1", "avif"),
    ],
)
def test_photo_service_detects_phone_and_tablet_formats(tmp_path, name, content, extension):
    prepared = PhotoAttachmentService.prepare_uploads([upload(name, content)])

    assert len(prepared) == 1
    assert prepared[0].filename.endswith(f".{extension}")
    assert prepared[0].content_type.startswith("image/")


def test_photo_service_deduplicates_removes_allows_many_and_enforces_total_size(tmp_path, monkeypatch):
    bundle = tmp_path / "draft"
    bundle.mkdir()
    excel = bundle / "draft.xlsx"
    excel.write_bytes(b"excel")
    service = PhotoAttachmentService(excel)

    prepared = service.prepare_uploads([
        upload("one.jpg", JPEG),
        upload("duplicate.jpg", JPEG),
        upload("two.png", PNG),
    ])
    saved = service.apply(prepared, [])

    assert len(saved) == 2
    assert len(list((bundle / PHOTO_FOLDER_NAME).iterdir())) == 2
    remaining = service.apply([], [str(saved[0]["id"])])
    assert len(remaining) == 1

    additions = service.prepare_uploads([
        upload(f"{index}.jpg", JPEG + bytes([index]))
        for index in range(25)
    ])
    service.validate_changes(additions, [])

    import src.services.photo_attachment_service as photo_module

    monkeypatch.setattr(photo_module, "MAX_TOTAL_PHOTO_BYTES", 10)
    with pytest.raises(PhotoAttachmentError, match="50 MB"):
        service.validate_changes(
            service.prepare_uploads([upload("large.jpg", JPEG + b"more")]),
            [str(remaining[0]["id"])],
        )


@pytest.fixture
def photo_web_app(tmp_path, monkeypatch):
    import src.services.archive_service as archive_module
    import src.web.application as application_module

    active = tmp_path / "Activas"
    archived = tmp_path / "Arquivadas"
    canceled = tmp_path / "Canceladas"
    active.mkdir()
    source = active / "2026_7001.xlsx"
    shutil.copy2(FIXTURE, source)

    monkeypatch.setattr(archive_module, "EXCEL_ARQUIVADAS_DIR", archived)
    monkeypatch.setattr(archive_module, "EXCEL_CANCELADAS_DIR", canceled)
    monkeypatch.setattr(application_module, "ACTIVE_AUTH_PROVIDER", "none")
    monkeypatch.setattr(application_module, "AUTH_ENABLED", False)
    monkeypatch.setattr(application_module, "MAIL_ENABLED", False)
    monkeypatch.setattr(application_module, "TEAMS_NOTIFICATIONS_ENABLED", False)
    monkeypatch.setattr(application_module, "STORAGE_BACKEND", "local")

    app = create_app(
        file_service=FileService(active),
        archive_service=ArchiveService(),
        editing_state_service=EditingStateService(tmp_path / "editing"),
    )
    return app.test_client(), active, archived


def acquire(client, name: str, client_id: str) -> dict:
    response = client.post(f"/api/file/{name}/lease", json={"client_id": client_id})
    assert response.status_code == 200, response.get_json()
    return response.get_json()["editing"]


def metadata(editing: dict, client_id: str) -> dict:
    return {
        "document_id": editing["document_id"],
        "client_id": client_id,
        "lease_token": editing["lease"]["token"],
        "base_revision": editing["revision"],
        "idempotency_key": str(uuid.uuid4()),
    }


def test_multipart_photos_stay_outside_report_and_follow_archive(photo_web_app):
    client, active, archived = photo_web_app
    editing = acquire(client, "2026_7001", "original-tab")
    draft_document = {
        "customer_name": "Cliente Fotografia",
        "intervention_report": "Relat\u00f3rio sem anexos incorporados",
        "technician_records": [{"technician": "Jo\u00e3o Freire"}],
        "Assinatura Cliente": SIGNATURE_DATA_URL,
        "_edit": metadata(editing, "original-tab"),
    }
    draft_response = client.post(
        "/api/file/2026_7001/draft",
        data={
            "document": json.dumps(draft_document),
            "removed_photo_ids": "[]",
            "photos": [
                (io.BytesIO(JPEG), "obra.jpg"),
                (io.BytesIO(PNG), "quadro.png"),
            ],
        },
        content_type="multipart/form-data",
    )
    result = draft_response.get_json()

    assert draft_response.status_code == 200, result
    assert len(result["photos"]) == 2
    draft_name = result["file"]
    draft_dir = active / draft_name
    photo_dir = draft_dir / PHOTO_FOLDER_NAME
    assert len(list(photo_dir.glob("fotografia_*"))) == 2
    document_data = json.loads(
        (draft_dir / f"{draft_name}__documento.json").read_text(encoding="utf-8")
    )
    assert "photos" not in document_data
    assert "photographs" not in document_data

    bootstrap = client.get(f"/api/file/{draft_name}/bootstrap?client_id=draft-tab")
    assert bootstrap.status_code == 200
    photos = bootstrap.get_json()["photos"]
    photo_response = client.get(photos[0]["url"])
    assert photo_response.status_code == 200
    assert photo_response.mimetype.startswith("image/")
    photo_response.close()

    draft_editing = bootstrap.get_json()["editing"]
    remove_response = client.post(
        f"/api/file/{draft_name}/draft",
        data={
            "document": json.dumps({
                **draft_document,
                "_edit": metadata(draft_editing, "draft-tab"),
            }),
            "removed_photo_ids": json.dumps([photos[0]["id"]]),
        },
        content_type="multipart/form-data",
    )
    assert remove_response.status_code == 200, remove_response.get_json()
    assert len(remove_response.get_json()["photos"]) == 1

    send_editing = acquire(client, draft_name, "draft-tab")
    send_response = client.post(
        f"/api/file/{draft_name}/send",
        json={
            "customer_name": "Cliente Fotografia",
            "customer_signer_name": "Maria Santos",
            "customer_signature_date": "2026-08-06",
            "intervention_report": "Relat\u00f3rio sem anexos incorporados",
            "technician_records": [{"technician": "Jo\u00e3o Freire", "start_time": "09:00", "end_time": "10:00", "date": "2026-08-06"}],
            "_edit": metadata(send_editing, "draft-tab"),
        },
    )
    assert send_response.status_code == 200, send_response.get_json()

    archive_dir = archived / draft_name
    archived_photos = list((archive_dir / PHOTO_FOLDER_NAME).glob("fotografia_*"))
    assert len(archived_photos) == 1
    archived_document = json.loads(
        (archive_dir / f"{draft_name}__documento.json").read_text(encoding="utf-8")
    )
    archived_html = (archive_dir / f"{draft_name}__folha_final.html").read_text(
        encoding="utf-8"
    )
    assert "photos" not in archived_document
    assert archived_photos[0].name not in archived_html
    assert "Fotografias" not in archived_html


def test_invalid_photo_is_rejected_before_creating_draft(photo_web_app):
    client, active, _archived = photo_web_app
    editing = acquire(client, "2026_7001", "invalid-tab")
    response = client.post(
        "/api/file/2026_7001/draft",
        data={
            "document": json.dumps({
                "customer_name": "Cliente",
                "technician_records": [{"technician": "Jo\u00e3o Freire"}],
                "_edit": metadata(editing, "invalid-tab"),
            }),
            "removed_photo_ids": "[]",
            "photos": (io.BytesIO(b"not-an-image"), "fake.jpg"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert "n\u00e3o \u00e9 suportado" in response.get_json()["error"]
    assert list(active.iterdir()) == [active / "2026_7001.xlsx"]


def test_graph_active_bundle_uploads_and_prunes_photo_subfolder(tmp_path, monkeypatch):
    bundle = tmp_path / "draft"
    bundle.mkdir()
    excel = bundle / "draft.xlsx"
    excel.write_bytes(b"excel")
    (bundle / ".graph_bundle.json").write_text('{"id": "bundle"}', encoding="utf-8")
    photo = PhotoAttachmentService.prepare_uploads([upload("obra.jpg", JPEG)])[0]
    PhotoAttachmentService(excel).apply([photo], [])

    service = GraphStorageService(GraphConfig("tenant", "client", "secret", "drive"))
    uploaded: list[str] = []
    deleted: list[str] = []
    stale_name = f"fotografia_{'a' * 64}.jpg"

    monkeypatch.setattr(service, "validate_config", lambda: None)
    monkeypatch.setattr(service, "ensure_folder_path", lambda _path: None)
    monkeypatch.setattr(
        service,
        "_get_item_by_path",
        lambda path: (
            {"id": "photo-folder", "name": PHOTO_FOLDER_NAME}
            if path.endswith(f"/{PHOTO_FOLDER_NAME}")
            else {"id": "bundle", "name": bundle.name}
        ),
    )
    monkeypatch.setattr(
        service,
        "upload_file",
        lambda local, remote, expected_etag=None: (
            uploaded.append(remote)
            or {"id": local.name, "name": local.name, "eTag": "new"}
        ),
    )
    monkeypatch.setattr(
        service,
        "_list_children",
        lambda item_id: ([
            {"id": "stale", "name": stale_name, "file": {"mimeType": "image/jpeg"}, "eTag": "old"},
            {"id": "keep", "name": "manual-note.txt", "file": {"mimeType": "text/plain"}, "eTag": "old"},
        ] if item_id == "photo-folder" else []),
    )
    monkeypatch.setattr(
        service,
        "_delete_item_by_id",
        lambda item_id, expected_etag=None: deleted.append(item_id),
    )

    result = service.upload_active_bundle(excel)

    assert any(f"/{PHOTO_FOLDER_NAME}/fotografia_" in path for path in result)
    assert uploaded == result
    assert deleted == ["stale"]


def test_graph_download_restores_photo_subfolder(tmp_path, monkeypatch):
    service = GraphStorageService(GraphConfig("tenant", "client", "secret", "drive"))
    local = tmp_path / "Activas"
    photo_name = f"fotografia_{'b' * 64}.jpg"

    def list_children(item_id: str):
        if item_id == "bundle-id":
            return [
                {"id": "excel-id", "name": "draft.xlsx", "file": {"mimeType": "application/xlsx"}, "size": 5},
                {"id": "photos-id", "name": PHOTO_FOLDER_NAME, "folder": {"childCount": 1}},
            ]
        return [
            {"id": "photo-id", "name": photo_name, "file": {"mimeType": "image/jpeg"}, "size": len(JPEG)}
        ]

    def download(item_id: str, destination: Path):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(JPEG if item_id == "photo-id" else b"excel")

    monkeypatch.setattr(service, "_list_children", list_children)
    monkeypatch.setattr(service, "download_item", download)

    excel = service._sync_active_bundle(
        {"id": "bundle-id", "name": "draft", "folder": {}, "eTag": "folder"},
        local,
    )

    assert excel == local / "draft" / "draft.xlsx"
    assert (local / "draft" / PHOTO_FOLDER_NAME / photo_name).read_bytes() == JPEG

def test_graph_queue_keeps_nested_photo_metadata_paths(tmp_path):
    original_dir = tmp_path / "live" / "draft"
    staged_dir = tmp_path / "staged" / "draft"
    original_photo_dir = original_dir / PHOTO_FOLDER_NAME
    staged_photo_dir = staged_dir / PHOTO_FOLDER_NAME
    original_photo_dir.mkdir(parents=True)
    staged_photo_dir.mkdir(parents=True)
    original_excel = original_dir / "draft.xlsx"
    staged_excel = staged_dir / "draft.xlsx"
    original_excel.write_bytes(b"excel")
    staged_excel.write_bytes(b"excel")

    metadata_name = f"fotografia_{'c' * 64}.jpg.graph.json"
    original_metadata = original_photo_dir / metadata_name
    staged_metadata = staged_photo_dir / metadata_name
    original_metadata.write_text('{"eTag":"old"}', encoding="utf-8")

    queue = GraphSyncQueue(None, tmp_path / "queue" / "graph-sync.sqlite3")
    payload = {
        "draft_path": str(staged_excel),
        "_original_draft_path": str(original_excel),
    }
    queue._hydrate_staging_metadata(payload)
    assert staged_metadata.read_text(encoding="utf-8") == '{"eTag":"old"}'

    staged_metadata.write_text('{"eTag":"new"}', encoding="utf-8")
    queue._publish_staging_metadata(payload)
    assert original_metadata.read_text(encoding="utf-8") == '{"eTag":"new"}'
