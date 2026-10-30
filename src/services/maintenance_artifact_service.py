"""Create all demo PDFs inside finalization staging, before publishing the bundle."""
import json
from pathlib import Path
from flask import current_app, render_template
from src.maintenance_schema import DEFINITION
from src.services.document_style_service import load_document_assets


def render_maintenance_document(document, site, *, draft_preview=False, auto_print=False):
    styles, logo = load_document_assets(current_app.static_folder, page_context=f"FS {document['service_number']} · SADI · {site['location']}")
    styles += "\n" + (Path(current_app.static_folder) / "css" / "maintenance-document.css").read_text(encoding="utf-8")
    return render_template("maintenance_document.html", document=document, site=site, definition=DEFINITION,
                           embedded_styles=styles, logo_src=logo, draft_preview=draft_preview, auto_print=auto_print)


def build_maintenance_bundle(staged, document, renderer):
    files = [{"name": renderer.export_archive_pdf(staged).name, "label": "Folha de serviço",
              "delivery": "customer_email"}]
    for index, site in enumerate(document["maintenance_checklists"], 1):
        html = staged.with_name(f"{staged.stem}__SADI_{index:02d}_{site['id']}.html")
        html.write_text(render_maintenance_document(document, site), encoding="utf-8")
        pdf = renderer.export_html_pdf(html, html.with_suffix(".pdf"))
        files.append({"name": pdf.name, "label": f"SADI — {site['location']}",
                      "delivery": "archive_only"})
    (staged.parent / "maintenance-manifest.json").write_text(json.dumps(files, ensure_ascii=False), encoding="utf-8")
    return files


def build_private_maintenance_bundle(staged, document, renderer, directory):
    """Render the complete set outside the Graph-synchronised archive tree."""
    staged, directory = Path(staged), Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    service_html = staged.with_name(f"{staged.stem}__folha_final.html")
    service_pdf = renderer.export_html_pdf(
        service_html, directory / f"{staged.stem}__folha_final.pdf"
    )
    files = [{"name": service_pdf.name, "label": "Folha de serviço",
              "delivery": "customer_email"}]
    for index, site in enumerate(document["maintenance_checklists"], 1):
        html = directory / f"{staged.stem}__SADI_{index:02d}_{site['id']}.html"
        html.write_text(render_maintenance_document(document, site), encoding="utf-8")
        pdf = renderer.export_html_pdf(html, html.with_suffix(".pdf"))
        files.append({"name": pdf.name, "label": f"SADI — {site['location']}",
                      "delivery": "archive_only"})
    (directory / "maintenance-manifest.json").write_text(
        json.dumps(files, ensure_ascii=False), encoding="utf-8"
    )
    return files
