"""SharePoint controls visibility; all workbooks and transports are synthetic."""

import json
import shutil
from pathlib import Path

import pytest

from src.services.active_file_index import ACTIVE_INDEX_NAME
from src.services.file_service import FileService
from src.services.graph_storage_service import GraphConfig, GraphStorageError, GraphStorageService


FIXTURE = Path(__file__).parent / "fixtures" / "test_sample.xlsx"


def workbook(name):
    return {"id": name, "name": name, "file": {"mimeType": "application/vnd.ms-excel"}, "eTag": "v1"}


def folder(name):
    return {"id": name, "name": name, "folder": {"childCount": 1}}


def cached(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(FIXTURE, path)
    return path


class FakeGraph(GraphStorageService):
    def __init__(self):
        super().__init__(GraphConfig("test", "test", "test", "test"))
        self.items = []
        self.children = {}

    def list_active_items(self):
        return self.items

    def _list_children(self, item_id):
        return self.children[item_id]

    def download_item(self, item_id, destination):
        cached(destination)


def test_65_cached_sheets_show_only_23_remote_bases_and_two_drafts(tmp_path, monkeypatch):
    graph = FakeGraph()
    for index in range(40):
        cached(tmp_path / f"old_{index:02}.xlsx")
    graph.items = [workbook(f"base_{index:02}.xlsx") for index in range(23)]
    for index in range(2):
        name = f"draft_{index}_2026-09-13_QA"
        graph.items.append(folder(name))
        graph.children[name] = [workbook(f"{name}.xlsx")]
    files = FileService(tmp_path, require_remote_index=True)
    assert files.list_excel_files() == []  # No unconfirmed cache on first start.
    graph.sync_active_files(tmp_path)
    assert len(list(tmp_path.rglob("*.xlsx"))) == 65  # Old local data preserved.
    visible = files.list_excel_files()
    assert len(visible) == 25
    assert not any(path.name.startswith("old_") for path in visible)
    assert FileService(tmp_path, require_remote_index=True).list_excel_files() == visible

    import src.web.application as web
    monkeypatch.setattr(web, "ACTIVE_AUTH_PROVIDER", "none")
    monkeypatch.setattr(web, "AUTH_ENABLED", False)
    app = web.create_app(file_service=files)
    response = app.test_client().get("/api/files?refresh=0")
    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload["files"]) == 25
    assert payload["html"].count('class="file-card ') == 25
    assert "old_" not in payload["html"]
    assert b"25 folha(s)" in app.test_client().get("/").data


def test_base_removal_preserves_a_remote_draft_and_hides_unpublished_work(tmp_path):
    graph = FakeGraph()
    draft = "base_2026-09-13_QA"
    local_draft = cached(tmp_path / draft / f"{draft}.xlsx")
    graph.items = [workbook("base.xlsx"), folder(draft)]
    graph.children[draft] = [workbook(local_draft.name)]
    graph.sync_active_files(tmp_path)
    assert len(FileService(tmp_path).list_excel_files()) == 2
    graph.items = [folder(draft)]
    graph.sync_active_files(tmp_path)
    assert FileService(tmp_path).list_excel_files() == [local_draft]
    graph.items = []
    graph.sync_active_files(tmp_path)
    assert FileService(tmp_path).list_excel_files() == []
    assert local_draft.read_bytes() == FIXTURE.read_bytes()
    # Publishing the same draft later makes it visible again.
    graph.items = [folder(draft)]
    graph.sync_active_files(tmp_path)
    assert FileService(tmp_path).list_excel_files() == [local_draft]


def test_removing_excel_inside_remaining_folder_hides_it(tmp_path):
    graph = FakeGraph()
    name = "draft_2026-09-13_QA"
    graph.items = [folder(name)]
    graph.children[name] = [workbook(f"{name}.xlsx")]
    graph.sync_active_files(tmp_path)
    path = tmp_path / name / f"{name}.xlsx"
    graph.children[name] = []
    graph.sync_active_files(tmp_path)
    assert FileService(tmp_path).list_excel_files() == []
    assert path.exists()


@pytest.mark.parametrize("failure", ["listing", "children", "download"])
def test_failed_refresh_preserves_last_confirmed_list(tmp_path, monkeypatch, failure):
    graph = FakeGraph()
    graph.items = [workbook("confirmed.xlsx")]
    graph.sync_active_files(tmp_path)
    cached(tmp_path / "old.xlsx")
    previous = (tmp_path / ACTIVE_INDEX_NAME).read_bytes()

    def fail(*args, **kwargs):
        raise GraphStorageError("Synthetic failure")

    if failure == "listing":
        monkeypatch.setattr(graph, "list_active_items", fail)
    elif failure == "children":
        graph.items = [folder("another")]
        monkeypatch.setattr(graph, "_list_children", fail)
    else:
        graph.items = [workbook("new.xlsx")]
        monkeypatch.setattr(graph, "download_item", fail)
    with pytest.raises(GraphStorageError):
        graph.sync_active_files(tmp_path)
    assert (tmp_path / ACTIVE_INDEX_NAME).read_bytes() == previous
    assert [path.name for path in FileService(tmp_path).list_excel_files()] == ["confirmed.xlsx"]






def test_local_backend_still_lists_local_files_and_invalid_index_hides_cache(tmp_path):
    path = cached(tmp_path / "local.xlsx")
    assert FileService(tmp_path, require_remote_index=False).list_excel_files() == [path]
    (tmp_path / ACTIVE_INDEX_NAME).write_text(json.dumps({"version": 1, "files": "invalid"}))
    assert FileService(tmp_path).list_excel_files() == []


def test_remote_pagination_includes_all_pages(tmp_path, monkeypatch):
    graph = FakeGraph()
    monkeypatch.setattr(graph, "list_active_items", lambda: GraphStorageService.list_active_items(graph))
    monkeypatch.setattr(graph, "_graph_json", lambda *args: {
        "value": [workbook("first.xlsx")],
        "@odata.nextLink": "https://graph.microsoft.com/v1.0/next",
    })
    monkeypatch.setattr(graph, "_request_json", lambda *args, **kwargs: {"value": [workbook("second.xlsx")]})
    graph.sync_active_files(tmp_path)
    assert [p.name for p in FileService(tmp_path).list_excel_files()] == ["first.xlsx", "second.xlsx"]


@pytest.mark.parametrize("listing", [
    {},
    {"value": "invalid"},
    {"value": [], "@odata.nextLink": "https://example.test/not-graph"},
])
def test_incomplete_or_invalid_inventory_cannot_replace_confirmed_list(tmp_path, monkeypatch, listing):
    graph = FakeGraph()
    graph.items = [workbook("confirmed.xlsx")]
    graph.sync_active_files(tmp_path)
    monkeypatch.setattr(graph, "list_active_items", lambda: GraphStorageService.list_active_items(graph))
    monkeypatch.setattr(graph, "_graph_json", lambda *args: listing)
    with pytest.raises(GraphStorageError):
        graph.sync_active_files(tmp_path)
    assert [p.name for p in FileService(tmp_path).list_excel_files()] == ["confirmed.xlsx"]


def test_refresh_preserves_local_files_even_with_graph_sidecars(tmp_path):
    graph = FakeGraph()
    graph.items = [workbook("removed.xlsx")]
    graph.sync_active_files(tmp_path)
    source = tmp_path / "removed.xlsx"
    before = source.read_bytes()
    graph.items = []
    graph.sync_active_files(tmp_path)
    assert FileService(tmp_path).list_excel_files() == []
    assert source.read_bytes() == before
    assert source.with_name(source.name + ".graph.json").exists()
