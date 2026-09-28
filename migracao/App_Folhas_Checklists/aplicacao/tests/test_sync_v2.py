import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.services.archive_service import ArchiveService
from src.services.editing_state_repository import SqliteStateRepository
from src.services.editing_state_service import EditingStateService, EditorIdentity
from src.services.file_service import FileService
from src.services.graph_sync_coordinator import GraphRefreshCoordinator
from src.services.graph_sync_queue import GraphSyncQueue
from src.web.application import create_app


FIXTURE = Path(__file__).parent / "fixtures" / "test_sample.xlsx"


@pytest.fixture(autouse=True)
def disable_web_auth(monkeypatch):
    import src.web.application as application_module

    monkeypatch.setattr(application_module, "ACTIVE_AUTH_PROVIDER", "none")
    monkeypatch.setattr(application_module, "AUTH_ENABLED", False)
    monkeypatch.setattr(application_module, "STORAGE_BACKEND", "local")


def make_active_file(root: Path, name: str) -> Path:
    active = root / "Excel" / "Activas"
    active.mkdir(parents=True, exist_ok=True)
    destination = active / f"{name}.xlsx"
    shutil.copy2(FIXTURE, destination)
    return destination


def wait_until(predicate, *, timeout: float = 3) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("A condição assíncrona não terminou dentro do tempo esperado.")


def test_sqlite_repository_serializes_concurrent_updates(tmp_path):
    database = tmp_path / "editing-state.sqlite3"

    def increment(_index: int) -> None:
        repository = SqliteStateRepository(database)
        with repository.transaction("same-document", lambda: {"counter": 0}) as state:
            current = int(state.get("counter") or 0)
            time.sleep(0.005)
            state["counter"] = current + 1

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(increment, range(24)))

    repository = SqliteStateRepository(database)
    with repository.transaction("same-document", lambda: {"counter": 0}) as state:
        assert state["counter"] == 24
    assert not list(tmp_path.glob("*.lock"))


def test_private_workspace_keeps_autosave_when_source_cache_changes(tmp_path):
    source = make_active_file(tmp_path, "2026_6101")
    service = EditingStateService(tmp_path / "editing")
    identity = EditorIdentity("tech-a", "Técnico A")
    editing = service.acquire_private_workspace(source, identity, "browser-a")
    saved = service.save_autosave(
        document_id=editing["document_id"],
        identity=identity,
        client_id="browser-a",
        lease_token=editing["lease"]["token"],
        base_revision=editing["revision"],
        idempotency_key="save-a",
        document={"intervention_report": "Conteúdo privado"},
    )

    source.touch()
    reopened = service.acquire_private_workspace(source, identity, "browser-a")

    assert saved["workspace_kind"] == "private"
    assert reopened["server_document"]["intervention_report"] == "Conteúdo privado"
    assert reopened["source_changed_at"] is not None


def test_bootstrap_returns_effective_autosave_in_one_request(tmp_path):
    source = make_active_file(tmp_path, "2026_6102")
    app = create_app(
        file_service=FileService(source.parent),
        archive_service=ArchiveService(),
        editing_state_service=EditingStateService(tmp_path / "editing"),
    )
    client = app.test_client()

    first = client.get("/api/file/2026_6102/bootstrap?client_id=browser-a")
    editing = first.get_json()["editing"]
    saved = client.post(
        "/api/file/2026_6102/autosave",
        json={
            "document": {"intervention_report": "Recuperado no primeiro carregamento"},
            "_edit": {
                "document_id": editing["document_id"],
                "client_id": "browser-a",
                "lease_token": editing["lease"]["token"],
                "base_revision": editing["revision"],
                "idempotency_key": "autosave-bootstrap",
            },
        },
    )
    reopened = client.get("/api/file/2026_6102/bootstrap?client_id=browser-a")

    assert first.status_code == 200
    assert saved.status_code == 200
    assert reopened.status_code == 200
    assert reopened.get_json()["recovery_source"] == "server"
    assert (
        reopened.get_json()["document"]["intervention_report"]
        == "Recuperado no primeiro carregamento"
    )


def test_index_does_not_wait_for_sharepoint_refresh(tmp_path):
    source = make_active_file(tmp_path, "2026_6103")
    started = threading.Event()
    release = threading.Event()

    class SlowGraph:
        def sync_active_files(self, _directory):
            started.set()
            release.wait(timeout=2)
            return [source]

    coordinator = GraphRefreshCoordinator(
        SlowGraph(),
        source.parent,
        refresh_seconds=5,
    )
    app = create_app(
        file_service=FileService(source.parent),
        archive_service=ArchiveService(),
        editing_state_service=EditingStateService(tmp_path / "editing"),
        graph_refresh_coordinator=coordinator,
    )
    client = app.test_client()

    before = time.monotonic()
    response = client.get("/")
    elapsed = time.monotonic() - before

    assert response.status_code == 200
    assert elapsed < 1.5
    assert started.wait(timeout=1)
    assert coordinator.status()["in_progress"] is True
    release.set()
    wait_until(lambda: not coordinator.status()["in_progress"])
    assert coordinator.status()["last_error"] is None


def test_graph_queue_retries_from_immutable_snapshot(tmp_path):
    draft_dir = tmp_path / "2026_6104_2026-07-21_TA"
    draft_dir.mkdir()
    source = draft_dir / f"{draft_dir.name}.xlsx"
    source.write_bytes(b"draft-v1")

    class FlakyGraph:
        def __init__(self):
            self.calls = []

        def upload_active_bundle(self, path, *, fail_if_exists=False):
            self.calls.append((Path(path), fail_if_exists, Path(path).read_bytes()))
            if len(self.calls) == 1:
                raise RuntimeError("falha transitória")
            return [f"Activas/{Path(path).parent.name}/{Path(path).name}"]

        def upload_archive_bundle(self, _path):
            return []

        def remove_active_name(self, _name, *, expected_etag=None):
            return True

    graph = FlakyGraph()
    queue = GraphSyncQueue(graph, tmp_path / "graph-sync.sqlite3")
    queued = queue.enqueue(
        "upload_active",
        {"draft_path": str(source), "fail_if_exists": True},
        job_id="draft:stable-test",
    )
    job_id = queued["id"]
    wait_until(lambda: queue.status(job_id)["status"] == "failed")
    staging_dir = Path(queue.status(job_id)["payload"]["_staging_dir"])
    shutil.rmtree(draft_dir)

    with queue._connect() as connection:
        connection.execute(
            "UPDATE graph_sync_jobs SET next_attempt_at = 0 WHERE id = ?",
            (job_id,),
        )
    wait_until(lambda: not queue._worker_running)
    queue.start()
    wait_until(lambda: queue.status(job_id)["status"] == "complete")

    assert [call[1] for call in graph.calls] == [True, True]
    assert all(call[0] != source for call in graph.calls)
    assert all(call[2] == b"draft-v1" for call in graph.calls)
    assert not staging_dir.exists()
    assert queue.summary()["complete"] == 1


def test_graph_queue_can_be_processed_outside_the_web_process(tmp_path):
    draft_dir = tmp_path / "2026_6106_2026-07-21_TA"
    draft_dir.mkdir()
    source = draft_dir / f"{draft_dir.name}.xlsx"
    source.write_bytes(b"draft")

    class RecordingGraph:
        def __init__(self):
            self.calls = []

        def upload_active_bundle(self, path, *, fail_if_exists=False):
            self.calls.append((Path(path), fail_if_exists))
            return [Path(path).name]

    graph = RecordingGraph()
    queue = GraphSyncQueue(
        graph,
        tmp_path / "graph-sync.sqlite3",
        auto_start=False,
    )
    job = queue.enqueue("upload_active", {"draft_path": str(source)})

    assert queue.status(job["id"])["status"] == "pending"
    assert graph.calls == []

    assert queue.run_until_idle() is True
    assert queue.status(job["id"])["status"] == "complete"
    assert len(graph.calls) == 1


def test_graph_queue_publishes_etag_metadata_back_to_live_draft(tmp_path):
    draft_dir = tmp_path / "2026_6105_2026-07-21_TA"
    draft_dir.mkdir()
    source = draft_dir / f"{draft_dir.name}.xlsx"
    source.write_bytes(b"draft")

    class MetadataGraph:
        def upload_active_bundle(self, path, *, fail_if_exists=False):
            path = Path(path)
            (path.parent / ".graph_bundle.json").write_text(
                '{"eTag":"folder-etag"}', encoding="utf-8"
            )
            path.with_name(f"{path.name}.graph.json").write_text(
                '{"eTag":"file-etag"}', encoding="utf-8"
            )
            return [path.name]

    queue = GraphSyncQueue(MetadataGraph(), tmp_path / "graph-sync.sqlite3")
    job = queue.enqueue(
        "upload_active",
        {"draft_path": str(source), "fail_if_exists": True},
        job_id="draft:metadata-test",
    )
    wait_until(lambda: queue.status(job["id"])["status"] == "complete")

    assert (draft_dir / ".graph_bundle.json").read_text(encoding="utf-8") == (
        '{"eTag":"folder-etag"}'
    )
    assert source.with_name(f"{source.name}.graph.json").exists()


def test_graph_queue_serializes_workers_using_same_database(tmp_path):
    active_calls = 0
    maximum_active_calls = 0
    guard = threading.Lock()

    class OrderedGraph:
        def upload_active_bundle(self, path, *, fail_if_exists=False):
            nonlocal active_calls, maximum_active_calls
            with guard:
                active_calls += 1
                maximum_active_calls = max(maximum_active_calls, active_calls)
            time.sleep(0.1)
            with guard:
                active_calls -= 1
            return [Path(path).name]

    graph = OrderedGraph()
    database = tmp_path / "graph-sync.sqlite3"
    first_queue = GraphSyncQueue(graph, database)
    second_queue = GraphSyncQueue(graph, database)
    jobs = []
    for index, queue in enumerate((first_queue, second_queue), start=1):
        draft_dir = tmp_path / f"2026_611{index}_2026-07-21_TA"
        draft_dir.mkdir()
        source = draft_dir / f"{draft_dir.name}.xlsx"
        source.write_bytes(f"draft-{index}".encode("ascii"))
        jobs.append(
            queue.enqueue(
                "upload_active",
                {"draft_path": str(source)},
                job_id=f"draft:ordered-{index}",
            )["id"]
        )

    wait_until(
        lambda: all(
            first_queue.status(job_id)["status"] == "complete"
            for job_id in jobs
        ),
        timeout=5,
    )
    assert maximum_active_calls == 1
