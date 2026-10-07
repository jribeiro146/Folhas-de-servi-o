"""Concurrent cache removal must not break the list or expose excluded sheets."""
import json
import shutil
from pathlib import Path

import pytest

from src.services.active_file_index import ACTIVE_INDEX_NAME
from src.services.editing_state_service import EditingStateService
from src.services.excel_service import ExcelService
from src.services.file_service import FileService
from src.web.application import create_app

FIXTURE = Path(__file__).parent / "fixtures" / "test_sample.xlsx"


def files(tmp_path):
    directory = tmp_path / "active"
    directory.mkdir()
    removed = directory / "removed.xlsx"
    kept = directory / "kept.xlsx"
    for path in (removed, kept):
        shutil.copy2(FIXTURE, path)
    return FileService(directory, require_remote_index=False), removed, kept


@pytest.mark.parametrize("url", ["/api/files?refresh=0", "/"])
def test_concurrent_removal_after_enumeration_keeps_http_list_available(tmp_path, monkeypatch, url):
    service, removed, kept = files(tmp_path)
    enumerate_files = service.list_excel_files

    def remove_after_enumeration():
        paths = enumerate_files()
        removed.unlink(missing_ok=True)
        return paths

    monkeypatch.setattr(service, "list_excel_files", remove_after_enumeration)
    app = create_app(file_service=service, editing_state_service=EditingStateService(tmp_path / "editing"))
    result = app.test_client().get(url)
    assert result.status_code == 200
    assert "removed" not in result.get_data(as_text=True)
    assert "kept" in result.get_data(as_text=True)
    assert kept.is_file()


@pytest.mark.parametrize("stage", ["after_stat", "during_excel_read"])
def test_disappearing_file_is_skipped_instead_of_invalid_or_crashing(tmp_path, monkeypatch, stage):
    service, removed, kept = files(tmp_path)
    if stage == "after_stat":
        original = service._build_cache_key

        def cache_key(path):
            key = original(path)
            if path == removed:
                removed.unlink()
            return key

        monkeypatch.setattr(service, "_build_cache_key", cache_key)
    else:
        original = ExcelService.read_link_as_form_data

        def read(excel):
            if excel.file_path == removed:
                removed.unlink()
            return original(excel)

        monkeypatch.setattr(ExcelService, "read_link_as_form_data", read)
    assert [entry["path"] for entry in service.list_valid_files()] == [kept]
    assert removed not in service._entry_cache


def test_inventory_rechecked_before_returning_cached_entries(tmp_path, monkeypatch):
    service, removed, kept = files(tmp_path)
    index = service.directory / ACTIVE_INDEX_NAME
    index.write_text(json.dumps({"version": 1, "files": [removed.name, kept.name]}))
    assert len(service.list_valid_files()) == 2
    enumerate_files = service.list_excel_files

    def exclude_after_enumeration():
        paths = enumerate_files()
        index.write_text(json.dumps({"version": 1, "files": [kept.name]}))
        return paths

    monkeypatch.setattr(service, "list_excel_files", exclude_after_enumeration)
    assert [entry["path"] for entry in service.list_valid_files()] == [kept]
    assert removed.is_file(), "confirmed exclusion must not delete a local workbook"


def test_disappearing_bundle_does_not_break_other_entries(tmp_path, monkeypatch):
    service, _, kept = files(tmp_path)
    bundle = service.directory / "synthetic-bundle"
    bundle.mkdir()
    original = Path.iterdir

    def iterate(path):
        if path == bundle:
            raise FileNotFoundError("synthetic concurrent bundle removal")
        return original(path)

    monkeypatch.setattr(Path, "iterdir", iterate)
    assert kept in service.list_excel_files()


def test_permission_failure_is_not_silently_treated_as_remote_removal(tmp_path, monkeypatch):
    service, _, _ = files(tmp_path)
    monkeypatch.setattr(service, "_build_cache_key", lambda _path: (_ for _ in ()).throw(PermissionError("synthetic permission error")))
    with pytest.raises(PermissionError):
        service.list_valid_files()


def test_existing_invalid_excel_is_still_reported(tmp_path):
    service, removed, kept = files(tmp_path)
    removed.write_bytes(b"synthetic invalid workbook")
    entries = {entry["path"]: entry for entry in service.list_valid_files()}
    assert entries[removed]["status"] == "invalid"
    assert entries[kept]["valid"] is True
