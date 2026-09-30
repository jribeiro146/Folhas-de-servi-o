"""Real SQLite queue with synthetic files and recording transports only."""

import json
from pathlib import Path

import pytest

from src.services.editing_state_repository import SqliteStateRepository
from src.services.graph_sync_queue import GraphSyncQueue
from src.services.local_changes import dirty_version, mark_dirty


@pytest.mark.parametrize("changed_during_upload", [False, True])
def test_confirmed_snapshot_clears_only_its_own_dirty_version(tmp_path, changed_during_upload):
    source = draft_file(tmp_path)
    first = mark_dirty(source.parent)
    versions = []

    class Graph:
        def upload_active_bundle(self, staged, **_kwargs):
            assert staged != source and staged.read_bytes() == b"synthetic-draft"
            assert dirty_version(staged.parent) == first
            if changed_during_upload:
                source.write_bytes(b"newer-unsaved-to-graph-version")
                versions.append(mark_dirty(source.parent))
            return [staged.name]

    queue = GraphSyncQueue(Graph(), tmp_path / "queue.sqlite3", auto_start=False)
    job = queue.enqueue("upload_active", {"draft_path": str(source)})
    queue.run_until_idle()
    assert queue.status(job["id"])["status"] == "complete"
    assert dirty_version(source.parent) == (versions[0] if changed_during_upload else "")


def draft_file(root, name="2026_9900_2026-09-28_TF"):
    directory = root / name
    directory.mkdir()
    source = directory / (name + ".xlsx")
    source.write_bytes(b"synthetic-draft")
    return source


@pytest.mark.parametrize("status,blocked", [
    ("pending", True), ("running", True), ("failed", True),
    ("held", True), ("complete", False),
])
def test_upload_lookup_uses_original_snapshot_path_without_changing_jobs(tmp_path, status, blocked):
    source = draft_file(tmp_path)
    queue = GraphSyncQueue(None, tmp_path / "queue.sqlite3", auto_start=False)
    job = queue.enqueue("upload_active", {"draft_path": str(source)})
    assert Path(job["payload"]["draft_path"]) != source
    with queue._connect() as connection:
        connection.execute("UPDATE graph_sync_jobs SET status = ? WHERE id = ?", (status, job["id"]))
    before = queue.status(job["id"])
    assert queue.has_unfinished_upload(source) is blocked
    assert not queue.has_unfinished_upload(tmp_path / "another-draft.xlsx")
    assert queue.status(job["id"]) == before


def test_upload_lookup_supports_legacy_paths_and_ignores_archive_jobs(tmp_path):
    source = draft_file(tmp_path)
    queue = GraphSyncQueue(None, tmp_path / "queue.sqlite3", auto_start=False)
    assert not queue.has_unfinished_upload(source)
    queue.enqueue("archive_and_remove", {"draft_path": str(source)})
    assert not queue.has_unfinished_upload(source)
    job = queue.enqueue("upload_active", {"draft_path": str(source)})
    with queue._connect() as connection:
        connection.execute("UPDATE graph_sync_jobs SET payload_json = ? WHERE id = ?",
                           (json.dumps({"draft_path": str(source)}), job["id"]))
    assert queue.has_unfinished_upload(source)


def test_archive_and_mail_wait_for_commit_and_are_not_duplicated(tmp_path):
    editing = SqliteStateRepository(tmp_path / "editing.sqlite3")
    archive = draft_file(tmp_path)
    calls = []

    class Graph:
        def upload_archive_bundle(self, path):
            calls.append(("archive", path.name))
            return [path.name]

        def export_archive_pdf(self, path):
            pdf = path.with_suffix(".pdf")
            pdf.write_bytes(b"%PDF-1.4\nsynthetic service sheet")
            calls.append(("pdf", pdf.name))
            return pdf, pdf.name

        def remove_active_name(self, name, *, expected_etag=None):
            calls.append(("remove", name))
            return True

    class Mail:
        def send_prepared(self, prepared, attachment_path, *, operation_id):
            assert prepared == {"to": "recipient@example.invalid", "cc": [], "bcc": [], "subject": "[TESTE] Fictício"}
            assert attachment_path == archive.with_suffix(".pdf")
            assert attachment_path.read_bytes().startswith(b"%PDF-")
            calls.append(("mail", operation_id))
            return {"accepted": True}

    queue = GraphSyncQueue(Graph(), tmp_path / "queue.sqlite3", mail_service=Mail(), auto_start=False)
    payload = {"archived_path": str(archive), "source_name": "synthetic-source",
               "_commit_guard": {"database": str(editing.database_path), "document_id": "document", "operation_id": "finalize"},
               "mail": {"to": "recipient@example.invalid", "cc": [], "bcc": [], "subject": "[TESTE] Fictício"}}
    job = queue.enqueue("archive_and_remove", payload, job_id="send:synthetic")
    assert job["status"] == "held"
    assert queue._next_retry_delay() is not None
    for status in (None, "pending", "failed"):
        with editing.transaction("document", lambda: {}) as state:
            state["operations"] = {"finalize": {"status": status}}
        queue.run_until_idle()
        assert calls == [] and queue.status(job["id"])["status"] == "held"
    with editing.transaction("document", lambda: {}) as state:
        state["operations"]["finalize"]["status"] = "complete"
    queue.run_until_idle()
    assert queue.status(job["id"])["status"] == "complete"
    assert [kind for kind, _ in calls] == ["archive", "pdf", "remove", "mail"]
    assert queue.enqueue("archive_and_remove", payload, job_id=job["id"])["status"] == "complete"
    queue.run_until_idle()
    assert len(calls) == 4


@pytest.mark.parametrize("database_state", ["missing", "corrupt", "missing_document"])
def test_unknown_commit_never_releases_external_work(tmp_path, database_state):
    database = tmp_path / "editing.sqlite3"
    if database_state == "corrupt":
        database.write_bytes(b"not-a-database")
    elif database_state == "missing_document":
        SqliteStateRepository(database)
    queue = GraphSyncQueue(None, tmp_path / "queue.sqlite3", auto_start=False)
    job = queue.enqueue("archive_and_remove", {"_commit_guard": {
        "database": str(database), "document_id": "document", "operation_id": "finalize"}})
    queue.run_until_idle()
    result = queue.status(job["id"])
    assert result["status"] == "held" and result["attempts"] == 0
    if database_state == "missing":
        assert not database.exists()
