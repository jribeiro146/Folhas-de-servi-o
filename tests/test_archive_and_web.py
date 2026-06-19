import json
import shutil
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

import openpyxl
import pytest

from src.services.archive_service import ArchiveService
from src.services.file_service import FileService
from src.web.application import create_app

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SAMPLE_FILE = FIXTURE_DIR / "test_sample.xlsx"
SIGNATURE_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+XgnsAAAAASUVORK5CYII="
)


def copy_sample(destination_dir: Path, name: str = "test_sample.xlsx") -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / name
    workbook = openpyxl.load_workbook(SAMPLE_FILE)
    try:
        workbook.save(target)
    finally:
        workbook.close()
    return target


def get_fs_image_positions(workbook_path: Path) -> tuple[set[tuple[int, int]], float | None]:
    workbook = openpyxl.load_workbook(workbook_path)
    try:
        worksheet = workbook["FS"]
        positions: set[tuple[int, int]] = set()

        for image in getattr(worksheet, "_images", []):
            marker = getattr(getattr(image, "anchor", None), "_from", None)
            if marker is None:
                continue
            positions.add((marker.row + 1, marker.col + 1))

        return positions, worksheet.row_dimensions[63].height
    finally:
        workbook.close()


class DummyService:
    pass


@pytest.fixture
def isolated_dirs(monkeypatch):
    base_dir = Path(tempfile.mkdtemp(prefix=f"folhas-servico-{uuid.uuid4()}-"))
    active_dir = base_dir / "Excel" / "Activas"
    archived_dir = base_dir / "Excel" / "Arquivadas"
    canceled_dir = base_dir / "Excel" / "Canceladas"

    import src.services.archive_service as archive_module

    monkeypatch.setattr(archive_module, "EXCEL_ARQUIVADAS_DIR", archived_dir)
    monkeypatch.setattr(archive_module, "EXCEL_CANCELADAS_DIR", canceled_dir)
    try:
        yield {
            "active": active_dir,
            "archived": archived_dir,
            "canceled": canceled_dir,
        }
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)


def test_archive_service_moves_to_archived(isolated_dirs):
    source = copy_sample(isolated_dirs["active"], "sample.xlsx")
    service = ArchiveService()

    archived = service.archive(source)

    assert archived.exists()
    assert archived.parent == isolated_dirs["archived"] / "sample"
    assert not source.exists()


def test_web_api_flow_send_and_cancel(isolated_dirs):
    active_dir = isolated_dirs["active"]
    copy_sample(active_dir, "2026_4572.xlsx")

    app = create_app(
        file_service=FileService(active_dir),
        archive_service=ArchiveService(),
    )
    client = app.test_client()

    files_response = client.get("/api/files")
    files_payload = files_response.get_json()
    assert files_response.status_code == 200
    assert files_payload["success"] is True
    assert files_payload["files"][0]["name"] == "2026_4572"
    assert files_payload["files"][0]["summary"]["customer_name"] == "Hospital CUF Descobertas, SA"
    assert files_payload["files"][0]["summary"]["local_store"] == "CUF Descobertas"
    assert files_payload["files"][0]["summary"]["contact_label"] == "Pedido por"
    assert files_payload["files"][0]["summary"]["contact_name"] == "Sra. Jéssica Silv"
    assert "R. Mário Botas" in files_payload["files"][0]["summary"]["address"]
    assert files_payload["files"][0]["summary"]["phone"] == "910036247"

    get_response = client.get("/api/file/2026_4572")
    get_payload = get_response.get_json()
    assert get_response.status_code == 200
    assert get_payload["success"] is True
    assert get_payload["data"]["Cliente nome"]

    draft_payload = {
        "customer_name": "Cliente Teste",
        "requested_by": "Pedido Teste",
        "intervention_report": "Teste de rascunho",
        "technician_records": [
            {
                "technician": "João Freire",
                "start_time": "09:00",
                "end_time": "11:30",
                "date": "2026-04-17",
            }
        ],
        "materials": [
            {
                "ref": "MAT-01",
                "description": "Bateria",
                "qty": "1",
            }
        ],
        "Assinatura Cliente": SIGNATURE_DATA_URL,
    }
    draft_response = client.post("/api/file/2026_4572/draft", json=draft_payload)
    draft_result = draft_response.get_json()
    assert draft_response.status_code == 200
    assert draft_result["success"] is True
    assert draft_result["status"] == "in_progress"
    assert draft_result["created_copy"] is True
    assert draft_result["file"].startswith("2026_4572_")
    assert draft_result["file"].endswith("_JF")
    assert (isolated_dirs["active"] / "2026_4572.xlsx").exists()
    draft_dir = isolated_dirs["active"] / draft_result["file"]
    draft_excel = draft_dir / f"{draft_result['file']}.xlsx"
    assert draft_excel.exists()
    assert (draft_dir / f"{draft_result['file']}__documento.json").exists()
    assert (draft_dir / f"{draft_result['file']}__assinatura_cliente.png").exists()
    draft_positions, draft_row_height = get_fs_image_positions(draft_excel)
    assert (63, 22) in draft_positions
    assert draft_row_height >= 42

    send_response = client.post(
        f"/api/file/{draft_result['file']}/send",
        json={
            "customer_name": "Cliente Final",
            "intervention_report": "Fecho",
            "_internal_observations": "Nota interna para consulta da equipa.",
            "technician_records": [
                {
                    "technician": "João Freire",
                    "date": "2026-04-17",
                }
            ],
        },
    )
    send_payload = send_response.get_json()
    assert send_response.status_code == 200
    assert send_payload["success"] is True
    assert (isolated_dirs["active"] / "2026_4572.xlsx").exists()
    archived_dir = isolated_dirs["archived"] / draft_result["file"]
    assert (archived_dir / f"{draft_result['file']}.xlsx").exists()
    archived_html = archived_dir / f"{draft_result['file']}__folha_final.html"
    internal_notes = archived_dir / f"{draft_result['file']}__observacoes_internas.txt"
    document_json = archived_dir / f"{draft_result['file']}__documento.json"
    assert archived_html.exists()
    assert document_json.exists()
    assert internal_notes.exists()
    assert internal_notes.read_text(encoding="utf-8").strip() == "Nota interna para consulta da equipa."
    assert (archived_dir / f"{draft_result['file']}__assinatura_cliente.png").exists()
    html_content = archived_html.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in html_content
    assert "Cliente Final" in html_content
    assert "data:image/png;base64," in html_content
    assert "Nota interna para consulta da equipa." not in html_content
    assert "_internal_observations" not in json.loads(document_json.read_text(encoding="utf-8"))
    archived_positions, archived_row_height = get_fs_image_positions(
        archived_dir / f"{draft_result['file']}.xlsx"
    )
    assert (63, 22) in archived_positions
    assert archived_row_height >= 42

    copy_sample(active_dir, "2026_9999.xlsx")
    cancel_response = client.post("/api/file/2026_9999/cancel", json={})
    cancel_payload = cancel_response.get_json()
    assert cancel_response.status_code == 200
    assert cancel_payload["success"] is True
    assert (isolated_dirs["canceled"] / "2026_9999" / "2026_9999.xlsx").exists()


def test_web_api_rejects_missing_required_fields(isolated_dirs):
    active_dir = isolated_dirs["active"]
    copy_sample(active_dir, "2026_4572.xlsx")

    app = create_app(
        file_service=FileService(active_dir),
        archive_service=ArchiveService(),
    )
    client = app.test_client()

    response = client.post("/api/file/2026_4572/send", json={"intervention_report": "Sem cliente"})
    payload = response.get_json()

    assert response.status_code == 400
    assert payload["success"] is False
    assert "Cliente / Customer" in payload["missing_fields"]



def test_draft_does_not_require_all_fields(isolated_dirs):
    """O draft deve aceitar dados parciais sem validação de campos obrigatórios."""
    active_dir = isolated_dirs["active"]
    copy_sample(active_dir, "2026_4572.xlsx")

    app = create_app(
        file_service=FileService(active_dir),
        archive_service=ArchiveService(),
    )
    client = app.test_client()

    response = client.post(
        "/api/file/2026_4572/draft",
        json={"intervention_report": "Apenas rascunho parcial"},
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["success"] is True
    assert payload["status"] == "in_progress"
    assert payload["created_copy"] is True
    assert (isolated_dirs["active"] / "2026_4572.xlsx").exists()
    assert (isolated_dirs["active"] / payload["file"] / f"{payload['file']}.xlsx").exists()
    assert (isolated_dirs["active"] / payload["file"] / f"{payload['file']}__documento.json").exists()


def test_document_preview_returns_html(isolated_dirs):
    active_dir = isolated_dirs["active"]
    copy_sample(active_dir, "2026_4572.xlsx")

    app = create_app(
        file_service=FileService(active_dir),
        archive_service=ArchiveService(),
    )
    client = app.test_client()

    response = client.post(
        "/api/file/2026_4572/document-preview",
        json={
            "service_number": "2026_4572",
            "customer_name": "Cliente Preview",
            "intervention_report": "Relatório em HTML",
        },
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["success"] is True
    assert "<!DOCTYPE html>" in payload["html"]
    assert "Cliente Preview" in payload["html"]
    assert "Folha de Serviço" in payload["html"]
    assert "Relatório de Intervenção" in payload["html"]
    assert "Dados do cliente" in payload["html"]
    assert "Dados da instalação" in payload["html"]
    assert "NIF / N.º de IVA" in payload["html"]
    assert "Trabalhos a efetuar" in payload["html"]
    assert "Descrição" in payload["html"]
    assert "Registo de técnicos e horas" in payload["html"]
    assert "Técnico responsável" in payload["html"]
    assert "Relatorio tecnico da intervencao" not in payload["html"]
    assert "data:image/png;base64," in payload["html"]


def test_file_not_found_returns_404(isolated_dirs):
    """Pedir um ficheiro inexistente deve devolver 404."""
    active_dir = isolated_dirs["active"]
    active_dir.mkdir(parents=True, exist_ok=True)

    app = create_app(
        file_service=FileService(active_dir),
        archive_service=ArchiveService(),
    )
    client = app.test_client()

    response = client.get("/api/file/inexistente")
    payload = response.get_json()

    assert response.status_code == 404
    assert payload["success"] is False
