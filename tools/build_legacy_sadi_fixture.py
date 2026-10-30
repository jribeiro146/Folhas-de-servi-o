"""Reproduce synthetic private JSON with the exact production-base writer/schema.

Requires Git history containing BASE. No app, configuration or credentials imported.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import types

BASE = "1ede4d8a690593a5f2b1d2908f3670fc1d038d7d"
ROOT = Path(__file__).resolve().parents[1]


def historical_module(path):
    source = subprocess.check_output(["git", "show", f"{BASE}:{path}"], cwd=ROOT)
    module = types.ModuleType("legacy_fixture")
    exec(compile(source, f"{BASE}:{path}", "exec"), module.__dict__)
    return module, hashlib.sha256(source).hexdigest()


def main():
    schema, schema_hash = historical_module("src/maintenance_schema.py")
    writer, writer_hash = historical_module("src/services/maintenance_private_service.py")
    answers = lambda questions: {key: {"answer": "OK", "justification": ""} for key, _ in questions}
    site = {"id": "legacy-site-20260928", "version": schema.VERSION,
            "location": "Edifício fictício — Piso 1", "date": "2026-09-28",
            "technician": "Técnico fictício", "period": "quarterly",
            "coverage_areas": "Piso 1", "customer_not_present": True,
            "general": answers(schema.GENERAL), "peripherals": answers(schema.PERIPHERALS),
            "trials": answers(schema.TRIALS), "configuration": {},
            "observations": "Dados sintéticos de compatibilidade; nenhuma checklist real copiada."}
    for kind in schema.GROUPS:
        site["configuration"][kind] = True
        unit = {key: ("2" if field_type == "number" else "Equipamento fictício")
                for key, _, field_type in schema.FIELDS[kind]}
        unit.update(id="legacy-unit-" + kind, checks=answers(schema.DEFINITION[kind]))
        site[kind] = [unit]
    sites = schema.normalize_sites([site])
    assert schema.site_errors(sites[0]) == []
    destination = ROOT / "tests/fixtures"
    with tempfile.TemporaryDirectory(prefix="sadi-legacy-") as temporary:
        private = writer.MaintenancePrivateService(Path(temporary) / "sadi")
        private.write("synthetic-legacy-document", sites)
        content = private._path("synthetic-legacy-document").read_bytes()
    fixture = destination / "sadi-private-1ede4d8.json"
    fixture.write_bytes(content)
    provenance = {"base_commit": BASE, "document_id": "synthetic-legacy-document",
                  "writer_sha256": writer_hash, "schema_sha256": schema_hash,
                  "fixture_sha256": hashlib.sha256(content).hexdigest(), "synthetic": True}
    fixture.with_suffix(".provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(provenance))


if __name__ == "__main__":
    main()
