"""Report regressions: keep every supported material and the end of long text."""
from pathlib import Path

from flask import Flask, render_template
import pytest

from src.document_schema import normalize_document_payload, SERVICE_TYPE_OPTIONS, EQUIPMENT_OPTIONS
from src.services.document_style_service import load_document_assets

WEB = Path(__file__).resolve().parents[1] / "src/web"


@pytest.mark.parametrize("language", ["pt", "en"])
def test_report_keeps_twelve_materials_and_long_text(language):
    app = Flask(__name__, template_folder=str(WEB / "templates"))
    document = normalize_document_payload({
        "document_language": language, "service_number": "QA-0012", "customer_name": "Cliente fictício",
        "materials_used": True,
        "materials": [{"ref": f"MAT-{i:02}", "description": f"Peça fictícia {i}", "qty": str(i)} for i in range(1, 13)],
        "intervention_report": "Texto de teste. " * 1000 + "FIM-DO-RELATORIO",
        "client_not_present": True,
    })
    styles, logo = load_document_assets(WEB / "static", page_context="FS QA-0012")
    with app.app_context():
        html = render_template("service_document.html", document=document, file_name="QA-0012",
            signatures={}, responsible_technician="Técnico fictício", service_type_options=SERVICE_TYPE_OPTIONS,
            equipment_options=EQUIPMENT_OPTIONS, embedded_styles=styles, logo_src=logo, auto_print=False)
    for i in range(1, 13):
        assert html.count(f"MAT-{i:02}") == 1
    assert "FIM-DO-RELATORIO" in html
    assert "@bottom-left" in html
    assert "Cliente não presente" in html if language == "pt" else "Customer not present" in html
