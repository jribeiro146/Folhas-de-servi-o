"""Opt-in real Chromium PDF QA, always synthetic and isolated before imports.

Run: python -B tools/verify_sadi_pdf_demo.py
Requires development test dependencies. Does not start a web server or send mail.
"""
import json
from pathlib import Path
import sys
import uuid

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.test_runtime import configure, block_outbound_network, seed_synthetic_data


def main():
    root = configure()
    block_outbound_network()
    _, draft = seed_synthetic_data()
    from src.web.application import create_app
    from src.maintenance_schema import make_signature
    from src.document_schema import normalize_document_payload
    from tests.test_maintenance import complete_document, signature_image, checklist_photo

    app = create_app()
    client = app.test_client()
    editing = client.post(f"/api/file/{draft.stem}/lease", json={"client_id": "pdf-qa"}).get_json()["editing"]
    document = complete_document()
    document["service_number"] = "2026_9901"
    document["maintenance_checklists"][0]["photos"] = [checklist_photo(), checklist_photo(size=(650, 1200))]
    document["maintenance_checklists"][1]["photos"] = [checklist_photo(color="#f2ddad")]
    for site in document["maintenance_checklists"]:
        site["final_observations"] = "Manutenção de demonstração concluída.\nFotografias e observações pertencem exclusivamente a este local. " + ("Observação fictícia para verificar a quebra de linhas e a paginação. " * 12)
    document["maintenance_checklists"][0]["conventional"][0]["checks"]["B44"] = {
        "answer": "NC", "justification": "Tecla de demonstração indisponível; substituição recomendada."
    }
    document = normalize_document_payload(document)
    for site in document["maintenance_checklists"]:
        for role in ("technician", "customer"):
            site["signatures"][role] = make_signature(app.secret_key, document, site, role, "Pessoa fictícia", "2026-09-17", signature_image())
    document["_edit"] = {"document_id": editing["document_id"], "client_id": "pdf-qa", "lease_token": editing["lease"]["token"], "base_revision": editing["revision"], "idempotency_key": uuid.uuid4().hex}
    response = client.post(f"/api/file/{draft.stem}/send", json=document)
    assert response.status_code == 200, response.get_json()
    simulation = client.post(response.get_json()["maintenance_bundle_url"] + "/simulate").get_json()
    assert simulation["simulated"] and len(simulation["attachments"]) == 1 and len(simulation["stored_only"]) == 2
    files = list(root.rglob("*.pdf"))
    assert len(files) == 3 and all(path.read_bytes().startswith(b"%PDF-") for path in files)
    report = {"data": str(root), "pdfs": [str(path) for path in files], "simulation": simulation}
    (root / "sadi-qa.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
