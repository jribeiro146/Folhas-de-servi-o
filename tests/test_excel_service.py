"""
Folhas de Serviço — Testes do Excel Service.

Usa uma cópia do ficheiro Excel de exemplo como fixture.
Testa leitura, escrita e validação sem tocar em ficheiros de produção.
"""

import shutil
import tempfile
from pathlib import Path

import pytest

# Resolve o caminho da fixture
FIXTURE_DIR = Path(__file__).parent / "fixtures"
SAMPLE_FILE = FIXTURE_DIR / "test_sample.xlsx"


def get_test_copy():
    """Cria uma cópia temporária do ficheiro de teste."""
    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp.close()
    shutil.copy2(str(SAMPLE_FILE), tmp.name)
    return Path(tmp.name)


class TestExcelServiceValidation:
    """Testes de validação do ficheiro Excel."""

    def test_file_not_found(self):
        from src.services.excel_service import ExcelService
        with pytest.raises(FileNotFoundError):
            ExcelService("ficheiro_inexistente.xlsx")

    def test_valid_file(self):
        from src.services.excel_service import ExcelService
        service = ExcelService(SAMPLE_FILE)
        service.validate_sheets()  # Não deve lançar excepção

    def test_sheets_exist(self):
        import openpyxl
        wb = openpyxl.load_workbook(str(SAMPLE_FILE), read_only=True)
        assert "LINK" in wb.sheetnames
        assert "FS" in wb.sheetnames
        wb.close()


class TestExcelServiceRead:
    """Testes de leitura da sheet LINK."""

    def test_read_link_returns_dict(self):
        from src.services.excel_service import ExcelService
        service = ExcelService(SAMPLE_FILE)
        data = service.read_link()
        assert isinstance(data, dict)
        assert len(data) > 0

    def test_read_folha_numero(self):
        from src.services.excel_service import ExcelService
        service = ExcelService(SAMPLE_FILE)
        numero = service.get_folha_numero()
        assert numero is not None
        assert "2026_4572" in str(numero)

    def test_read_cliente_nome(self):
        from src.services.excel_service import ExcelService
        service = ExcelService(SAMPLE_FILE)
        data = service.read_link()
        # Coluna AE = Cliente nome
        assert data["AE"] is not None
        assert "CUF" in str(data["AE"])

    def test_read_checkbox_field(self):
        from src.services.excel_service import ExcelService
        service = ExcelService(SAMPLE_FILE)
        data = service.read_link()
        # Coluna M = MAN (marcado com "X" no ficheiro de exemplo)
        assert data["M"] == "X"

    def test_read_link_as_form_data(self):
        from src.services.excel_service import ExcelService
        service = ExcelService(SAMPLE_FILE)
        form_data = service.read_link_as_form_data()
        assert isinstance(form_data, dict)
        # Checkbox MAN deve ser True
        assert form_data["MAN"] is True
        # Checkbox ASSIST deve ser False (não marcado)
        assert form_data["ASSIST"] is False


class TestExcelServiceWrite:
    """Testes de escrita na sheet LINK."""

    def test_write_and_read_back(self):
        from src.services.excel_service import ExcelService
        test_file = get_test_copy()
        try:
            service = ExcelService(test_file)

            # Escrever um valor novo
            service.write_link({"BR": "Teste de escrita automática"})

            # Ler de volta
            data = service.read_link()
            assert data["BR"] == "Teste de escrita automática"
        finally:
            test_file.unlink(missing_ok=True)

    def test_write_does_not_touch_readonly(self):
        from src.services.excel_service import ExcelService
        test_file = get_test_copy()
        try:
            service = ExcelService(test_file)

            # Coluna A (Folha nº) é read_only
            original = service.read_link()["A"]
            service.write_link({"A": "VALOR_ALTERADO"})

            # Deve manter o valor original
            data = service.read_link()
            assert data["A"] == original
        finally:
            test_file.unlink(missing_ok=True)

    def test_write_checkbox(self):
        from src.services.excel_service import ExcelService
        test_file = get_test_copy()
        try:
            service = ExcelService(test_file)

            # Marcar ASSIST (coluna L)
            service.write_link({"L": "X"})

            data = service.read_link()
            assert data["L"] == "X"
        finally:
            test_file.unlink(missing_ok=True)


class TestFieldMap:
    """Testes de integridade do field_map."""

    def test_all_columns_unique(self):
        from src.field_map import FIELD_MAP
        columns = [f.column for f in FIELD_MAP]
        assert len(columns) == len(set(columns)), "Colunas duplicadas no field_map"

    def test_all_labels_unique(self):
        from src.field_map import FIELD_MAP
        labels = [f.label for f in FIELD_MAP]
        assert len(labels) == len(set(labels)), "Labels duplicados no field_map"

    def test_field_by_column_lookup(self):
        from src.field_map import FIELD_BY_COLUMN
        assert "A" in FIELD_BY_COLUMN
        assert FIELD_BY_COLUMN["A"].label == "Folha nº"

    def test_required_fields_exist(self):
        from src.field_map import REQUIRED_FIELDS
        assert len(REQUIRED_FIELDS) > 0
        labels = [f.label for f in REQUIRED_FIELDS]
        assert "Folha nº" in labels
        assert "Cliente nome" in labels
