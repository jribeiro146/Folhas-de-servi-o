"""Render long synthetic FS/SADI reports with the actual local PDF service.

This is an explicit visual QA command, not part of the mocked unit tests.
No operational configuration, application worker or external service is loaded.
"""
import argparse
import base64
import io
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.test_runtime import configure, block_outbound_network


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Directory for synthetic QA reports")
    args = parser.parse_args()
    runtime = configure()
    block_outbound_network()
    output = args.output.resolve() if args.output else runtime / "reports"
    output.mkdir(parents=True, exist_ok=True)

    from flask import Flask, render_template
    from PIL import Image, ImageDraw
    from src.document_schema import normalize_document_payload, SERVICE_TYPE_OPTIONS, EQUIPMENT_OPTIONS
    from src.services.document_style_service import load_document_assets
    from src.services.maintenance_artifact_service import render_maintenance_document
    from src.services.local_pdf_service import LocalPdfService

    source = ROOT.parent / "dados/cenarios/sadi-por-assinar.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload.update(service_number="QA-0012", customer_name="Cliente fictício para validação",
        materials_used=True, client_not_present=True,
        materials=[{"ref": f"MAT-{i:02}", "description": (f"Material sintético {i}. " * 8), "qty": str(i)} for i in range(1, 13)],
        intervention_report="\n".join(f"Linha {i:03}: Verificação sintética com observações extensas para confirmar a paginação e a preservação integral do relatório." for i in range(1, 101)) + "\nFIM-DO-RELATORIO")
    document = normalize_document_payload(payload)
    site = document["maintenance_checklists"][0]
    site["observations"] = "Observação sintética extensa. " * 200 + "FIM-DAS-OBSERVACOES"
    photo = Image.new("RGB", (640, 360), "#eef4fb")
    draw = ImageDraw.Draw(photo)
    draw.rectangle((180, 60, 460, 300), fill="#0b4a8b")
    draw.text((30, 25), "EQUIPAMENTO FICTICIO - QA", fill="#19344d")
    stream = io.BytesIO()
    photo.save(stream, format="PNG")
    image = "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode("ascii")
    site["photos"] = [{"id": f"qa-photo-{i}", "image": image, "caption": f"Fotografia fictícia {i:02}", "name": f"qa-{i}.png", "error": ""} for i in range(1, 11)]
    web = ROOT / "src/web"
    app = Flask(__name__, template_folder=str(web / "templates"), static_folder=str(web / "static"))
    styles, logo = load_document_assets(web / "static", page_context="FS QA-0012")
    with app.app_context():
        reports = {
            "folha-extensa": render_template("service_document.html", document=document, file_name="QA-0012", signatures={},
                responsible_technician="Técnico fictício", service_type_options=SERVICE_TYPE_OPTIONS,
                equipment_options=EQUIPMENT_OPTIONS, embedded_styles=styles, logo_src=logo, auto_print=False),
            "sadi-extensa": render_maintenance_document(document, site, draft_preview=True),
        }
    for name, html in reports.items():
        path = output / f"{name}.html"
        path.write_text(html, encoding="utf-8")
        pdf = LocalPdfService().export_html_pdf(path, path.with_suffix(".pdf"))
        print(json.dumps({"file": str(pdf), "bytes": pdf.stat().st_size, "synthetic": True}), flush=True)


if __name__ == "__main__":
    main()
