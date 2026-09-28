"""SharePoint controls visibility; all workbooks and transports are synthetic."""

import json
import shutil
from pathlib import Path

import pytest

from src.services.active_file_index import ACTIVE_INDEX_NAME, read_active_inventory
from src.services.file_service import FileService
from src.services.graph_storage_service import GraphConfig, GraphStorageError, GraphStorageService
from src.services.local_changes import mark_dirty


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
    mark_dirty(local_draft.parent)
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


@pytest.mark.parametrize("dirty", [False, True])
def test_removing_excel_inside_remaining_folder_hides_it(tmp_path, dirty):
    graph = FakeGraph()
    name = "draft_2026-09-13_QA"
    graph.items = [folder(name)]
    graph.children[name] = [workbook(f"{name}.xlsx")]
    graph.sync_active_files(tmp_path)
    path = tmp_path / name / f"{name}.xlsx"
    if dirty:
        mark_dirty(path.parent)
    graph.children[name] = []
    graph.sync_active_files(tmp_path)
    assert FileService(tmp_path).list_excel_files() == []
    if dirty:
        assert path.exists()


@pytest.mark.parametrize("failure", ["listing", "children"])
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
    with pytest.raises(GraphStorageError):
        graph.sync_active_files(tmp_path)
    assert (tmp_path / ACTIVE_INDEX_NAME).read_bytes() == previous
    assert [path.name for path in FileService(tmp_path).list_excel_files()] == ["confirmed.xlsx"]


def test_download_failure_publishes_complete_inventory_and_continues_other_downloads(tmp_path, monkeypatch):
    graph = FakeGraph()
    graph.items = [workbook("removed.xlsx")]
    graph.sync_active_files(tmp_path)
    # Keep an unowned old cache entry, proving visibility does not rely on deletion.
    cached(tmp_path / "old.xlsx")
    files = FileService(tmp_path, require_remote_index=True)
    assert [entry["name"] for entry in files.list_valid_files()] == ["removed"]
    graph.items = [workbook("new.xlsx"), workbook("available.xlsx")]

    def download(item_id, destination):
        inventory = read_active_inventory(tmp_path)
        assert inventory["files"] == {"new.xlsx", "available.xlsx"}
        assert not any(path.name in {"removed.xlsx", "old.xlsx"} for path in files.list_excel_files())
        if item_id == "new.xlsx":
            raise GraphStorageError("Synthetic download failure")
        cached(destination)

    monkeypatch.setattr(graph, "download_item", download)
    with pytest.raises(GraphStorageError, match="Inventário confirmado"):
        graph.sync_active_files(tmp_path)
    inventory = read_active_inventory(tmp_path)
    assert inventory["available"] is True
    assert inventory["unavailable_files"] == {"new.xlsx"}
    assert [entry["name"] for entry in files.list_valid_files()] == ["available"]
    assert FileService(tmp_path, require_remote_index=True).list_excel_files() == [tmp_path / "available.xlsx"]
    assert (tmp_path / "old.xlsx").exists()


def test_stale_content_is_not_ready_when_replacement_download_fails(tmp_path, monkeypatch):
    graph = FakeGraph()
    graph.items = [workbook("changed.xlsx")]
    graph.sync_active_files(tmp_path)
    original = (tmp_path / "changed.xlsx").read_bytes()
    graph.items[0]["eTag"] = "v2"
    monkeypatch.setattr(graph, "download_item", lambda *_: (_ for _ in ()).throw(GraphStorageError("Synthetic failure")))
    with pytest.raises(GraphStorageError):
        graph.sync_active_files(tmp_path)
    assert FileService(tmp_path, require_remote_index=True).list_excel_files() == []
    assert (tmp_path / "changed.xlsx").read_bytes() == original
    assert read_active_inventory(tmp_path)["files"] == {"changed.xlsx"}


def test_empty_remote_list_hides_but_preserves_downloaded_draft_and_attachments(tmp_path):
    graph = FakeGraph()
    draft_name = "draft_2026-09-28_QA"
    graph.items = [folder(draft_name)]
    graph.children[draft_name] = [workbook(f"{draft_name}.xlsx")]
    graph.sync_active_files(tmp_path)
    draft = tmp_path / draft_name
    attachment = draft / "synthetic.txt"
    attachment.write_text("synthetic attachment", encoding="utf-8")
    graph.items = []
    graph.sync_active_files(tmp_path)
    assert read_active_inventory(tmp_path)["available"] is True
    assert read_active_inventory(tmp_path)["files"] == set()
    assert FileService(tmp_path, require_remote_index=True).list_valid_files() == []
    assert (draft / f"{draft_name}.xlsx").is_file()
    assert attachment.read_text(encoding="utf-8") == "synthetic attachment"


@pytest.mark.parametrize("payload", [None, "not JSON", {"version": 2, "files": []},
                                    {"version": 1, "files": ["../old.xlsx"]},
                                    {"version": 1, "files": ["old.xlsx"], "unavailable_files": ["other.xlsx"]}])
def test_unavailable_inventory_never_lists_unconfirmed_cache(tmp_path, payload):
    cached(tmp_path / "old.xlsx")
    if payload is not None:
        (tmp_path / ACTIVE_INDEX_NAME).write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    assert FileService(tmp_path, require_remote_index=True).list_valid_files() == []
    assert read_active_inventory(tmp_path)["available"] is False


def test_unreadable_inventory_is_reported_without_cache_fallback(tmp_path, monkeypatch):
    cached(tmp_path / "old.xlsx")
    original = Path.read_text

    def read(path, *args, **kwargs):
        if path.name == ACTIVE_INDEX_NAME:
            raise PermissionError("synthetic unreadable inventory")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    assert FileService(tmp_path, require_remote_index=True).list_valid_files() == []
    assert read_active_inventory(tmp_path)["error"] == "invalid"


@pytest.mark.parametrize("failed_page", [None, "root", "draft"])
def test_paginated_base_and_draft_inventories_publish_only_after_every_page(tmp_path, monkeypatch, failed_page):
    graph = GraphStorageService(GraphConfig("test", "test", "test", "test"))
    previous = {"version": 1, "files": ["previous.xlsx"]}
    (tmp_path / ACTIVE_INDEX_NAME).write_text(json.dumps(previous), encoding="utf-8")
    original = (tmp_path / ACTIVE_INDEX_NAME).read_bytes()
    draft = "draft_2026-09-28_QA"
    root_next = "https://graph.microsoft.com/v1.0/root-next"
    draft_next = "https://graph.microsoft.com/v1.0/draft-next"

    def first_page(_method, path):
        if "/root:/" in path:
            return {"value": [workbook("base1.xlsx"), folder(draft)], "@odata.nextLink": root_next}
        return {"value": [workbook(f"{draft}.xlsx")], "@odata.nextLink": draft_next}

    def second_page(url, **_kwargs):
        if ((url == root_next and failed_page == "root")
                or (url == draft_next and failed_page == "draft")):
            raise GraphStorageError("Synthetic pagination failure")
        return {"value": [workbook("base2.xlsx" if url == root_next else "extra.xlsm")]}

    monkeypatch.setattr(graph, "_graph_json", first_page)
    monkeypatch.setattr(graph, "_request_json", second_page)
    monkeypatch.setattr(graph, "download_item", lambda _item, destination: cached(destination))
    if failed_page:
        with pytest.raises(GraphStorageError):
            graph.sync_active_files(tmp_path)
        assert (tmp_path / ACTIVE_INDEX_NAME).read_bytes() == original
        assert not list(tmp_path.rglob("*.xlsx"))
    else:
        graph.sync_active_files(tmp_path)
        assert read_active_inventory(tmp_path)["files"] == {
            "base1.xlsx", "base2.xlsx", f"{draft}/{draft}.xlsx", f"{draft}/extra.xlsm",
        }


def test_invalid_content_metadata_does_not_block_confirmed_removal(tmp_path):
    graph = FakeGraph()
    graph.items = [workbook("removed.xlsx"), workbook("kept.xlsx")]
    graph.sync_active_files(tmp_path)
    (tmp_path / "kept.xlsx.graph.json").write_text("[]", encoding="utf-8")
    graph.items = [workbook("kept.xlsx")]
    graph.sync_active_files(tmp_path)
    assert read_active_inventory(tmp_path)["files"] == {"kept.xlsx"}
    assert [file.name for file in FileService(tmp_path, require_remote_index=True).list_excel_files()] == ["kept.xlsx"]


def test_atomic_inventory_publication_retries_windows_reader_sharing_violation(tmp_path, monkeypatch):
    import src.services.graph_storage_service as storage

    destination = tmp_path / ACTIVE_INDEX_NAME
    previous = {"version": 1, "files": ["old.xlsx"]}
    replacement = {"version": 1, "files": []}
    destination.write_text(json.dumps(previous), encoding="utf-8")
    replace = storage.os.replace
    attempts = []

    def temporarily_busy(source, target):
        attempts.append(source)
        assert json.loads(destination.read_text(encoding="utf-8")) == previous
        if len(attempts) < 3:
            error = PermissionError("synthetic Windows reader")
            error.winerror = 32
            raise error
        return replace(source, target)

    monkeypatch.setattr(storage.os, "replace", temporarily_busy)
    monkeypatch.setattr(storage.time, "sleep", lambda _delay: None)
    GraphStorageService._atomic_write_json(destination, replacement)
    assert len(attempts) == 3
    assert len(set(attempts)) == 1
    assert json.loads(destination.read_text(encoding="utf-8")) == replacement
    assert not list(tmp_path.glob("*.tmp"))


def test_metadata_first_cleanup_does_not_leave_an_orphan_workbook(tmp_path, monkeypatch):
    graph = FakeGraph()
    path = cached(tmp_path / "removed.xlsx")
    metadata = path.with_name(path.name + ".graph.json")
    metadata.write_text("{}")
    original = Path.iterdir

    def ordered(directory):
        entries = list(original(directory))
        if directory == tmp_path:
            entries.sort(key=lambda entry: entry != metadata)
        return iter(entries)

    monkeypatch.setattr(Path, "iterdir", ordered)
    graph.sync_active_files(tmp_path)
    assert not path.exists()
    assert not metadata.exists()


def test_failed_local_removal_keeps_metadata_for_retry_and_hides_file(tmp_path, monkeypatch):
    graph = FakeGraph()
    graph.items = [workbook("removed.xlsx")]
    graph.sync_active_files(tmp_path)
    path = tmp_path / "removed.xlsx"
    graph.items = []
    remove = graph._remove_local_path
    monkeypatch.setattr(graph, "_remove_local_path", lambda target: None if target == path else remove(target))
    graph.sync_active_files(tmp_path)
    assert path.exists()
    assert path.with_name(path.name + ".graph.json").exists()
    assert FileService(tmp_path).list_excel_files() == []
    monkeypatch.setattr(graph, "_remove_local_path", remove)
    graph.sync_active_files(tmp_path)
    assert not path.exists()


def test_local_backend_still_lists_local_files_and_invalid_index_hides_cache(tmp_path):
    path = cached(tmp_path / "local.xlsx")
    assert FileService(tmp_path, require_remote_index=False).list_excel_files() == [path]
    (tmp_path / ACTIVE_INDEX_NAME).write_text(json.dumps({"version": 1, "files": "invalid"}))
    assert FileService(tmp_path).list_excel_files() == []
