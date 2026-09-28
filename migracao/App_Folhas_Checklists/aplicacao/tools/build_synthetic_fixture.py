"""Build the migration test fixture exclusively from code and fictitious values.

This script never reads an existing workbook, .env, application configuration,
customer records or network resources. The LINK field definitions are read as
Python syntax (AST); importing the application before test isolation is avoided.
Only tests/fixtures/test_sample.xlsx inside this copied application is written.
The service number is a stable test identifier, not an imported service record.
"""

from __future__ import annotations

import ast
from pathlib import Path

from openpyxl import Workbook
from openpyxl.comments import Comment


SYNTHETIC_NOTICE = (
    "Fixture totalmente sintética, criada a partir do mapa de campos e de "
    "valores fictícios. Não contém um Excel original nem dados de clientes."
)

# These fictitious values correspond to assertions in test_archive_and_web.py
# and test_excel_service.py. Keep input and expected values consistent.
VALUES = {
    "Folha nº": "2026_4572",
    "Pedido por": "Pessoa Pedido Fictícia",
    "Email": "cliente@example.test",
    "Telefone": "000000001",
    # Exercise fallback to the requester/customer phone in the list summary.
    "Contacto": "",
    "Telefone (2)": "",
    "MAN": "X",
    "Cliente nome": "Cliente Fictício de Demonstração",
    "Local": "Local Fictício",
    "Morada": "Rua Fictícia, 10",
    "Cód. Postal": "1000-001 Local Fictício",
    "CP": "10",
    "NIF": "000000000",
}


def read_field_definitions(source: Path) -> list[tuple[str, str]]:
    """Read literal FieldDef column/label pairs without executing source code."""
    tree = ast.parse(source.read_text(encoding="utf-8-sig"), filename=str(source))
    definitions = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "FieldDef":
            continue
        if len(node.args) < 2:
            raise ValueError("FieldDef requires a literal column and label.")
        column, label = (ast.literal_eval(argument) for argument in node.args[:2])
        if not isinstance(column, str) or not isinstance(label, str):
            raise ValueError("The field map must contain literal text identifiers.")
        definitions.append((column, label))
    if not definitions or len({column for column, _ in definitions}) != len(definitions):
        raise ValueError("The field map is empty or has duplicate columns.")
    return definitions


def build_fixture() -> Path:
    application_root = Path(__file__).resolve().parents[1]
    target = application_root / "tests" / "fixtures" / "test_sample.xlsx"
    if not target.resolve().is_relative_to(application_root):
        raise ValueError("The fixture target must stay inside the copied application.")
    definitions = read_field_definitions(application_root / "src" / "field_map.py")
    target.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    try:
        link = workbook.active
        link.title = "LINK"
        service_sheet = workbook.create_sheet("FS")
        service_sheet["A1"] = "FOLHA DE SERVIÇO — FIXTURE SINTÉTICA"
        service_sheet["A2"] = SYNTHETIC_NOTICE
        for column, label in definitions:
            # The old fixture contract includes no work-number header. Tests
            # create their own shifted work-number columns when they need one.
            if label == "N.º de obra":
                continue
            link[f"{column}2"] = label
            if label in VALUES:
                link[f"{column}3"] = VALUES[label]
        link["A3"].comment = Comment(SYNTHETIC_NOTICE, "Migração — dados sintéticos")
        link.sheet_state = "hidden"
        service_sheet.sheet_state = "visible"
        workbook.active = 1
        workbook.properties.creator = "Migração — gerador de fixture sintética"
        workbook.properties.lastModifiedBy = "Gerador sintético"
        workbook.properties.title = "Fixture sintética da aplicação de folhas de serviço"
        workbook.properties.subject = "Apenas testes isolados, sem dados operacionais"
        workbook.properties.description = SYNTHETIC_NOTICE
        workbook.save(target)
    finally:
        workbook.close()
    return target


if __name__ == "__main__":
    result = build_fixture()
    print(f"Fixture sintética criada: {result.relative_to(Path(__file__).resolve().parents[1])}")
