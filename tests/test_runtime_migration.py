"""Offline migration against synthetic historical stores and file bundles."""
import hashlib
import json
import sqlite3
from pathlib import Path
import pytest
from contextlib import closing

from tests.legacy_editing_shapes import legacy_lease, snapshot_name


def test_legacy_json_migration_preserves_reservation_and_all_bundle_bytes(tmp_path):
    from src.services.runtime_migration import prepare, apply
    from src.services.editing_state_service import EditingStateService
    from src.services.runtime_contract import RuntimeContract
    data = tmp_path / "data"
    editing = data / "editing-state"
    editing.mkdir(parents=True)
    state = legacy_lease("synthetic-document")
    raw = json.dumps(state).encode()
    snapshot = editing / snapshot_name(state["document_id"])
    snapshot.write_bytes(raw)
    excel = tmp_path / "Excel"
    bundle = excel / "Activas" / "draft"
    bundle.mkdir(parents=True)
    assets = {"draft.xlsx": b"synthetic workbook", "photo.jpg": b"synthetic photo",
              "draft.xlsx.graph.json": b'{"id":"remote-id","eTag":"v1"}',
              "signature.png": b"synthetic signature"}
    for name, value in assets.items():
        (bundle / name).write_bytes(value)
    sadi = data / "sadi"
    sadi.mkdir()
    (sadi / ("a" * 32 + ".json")).write_text('[{"synthetic":true}]')
    work = tmp_path / "migration"
    report = prepare(data, [excel], work, source_sha256="a" * 64)
    assert not (data / "runtime-contract.sqlite3").exists()
    assert report["editing_converted"] == 1
    apply(work, source_sha256="a" * 64)
    assert RuntimeContract(data, "a" * 64).inspect()["matches_release"]
    with closing(EditingStateService(editing).repository._connect()) as connection:
        result = json.loads(connection.execute("SELECT state_json FROM editing_states").fetchone()[0])
    assert result["document_id"] == state["document_id"]
    assert result["owner"]["owner_id"] == "synthetic-user"
    assert result["sessions"]["synthetic-token"]["expires_at"] == 2
    assert result["revision"] == 1
    assert snapshot.read_bytes() == raw
    assert {p.name: p.read_bytes() for p in bundle.iterdir()} == assets
    assert json.loads((sadi / ("a" * 32 + ".json")).read_text()) == [{"synthetic": True}]
    assert apply(work, source_sha256="a" * 64)["state"] == "complete"


@pytest.mark.parametrize("version", [0, 1, 2])
def test_legacy_queue_migration_keeps_receipts_and_never_requeues_attempts(tmp_path, version):
    from src.services.runtime_migration import prepare, apply
    from src.services.graph_sync_queue import GraphSyncQueue
    from tests import legacy_sqlite_format_v1 as v1, legacy_sqlite_format_v2 as v2
    data = tmp_path / "data"
    data.mkdir()
    schema = list(v1.SCHEMAS["queue"] if version < 2 else v2.SCHEMAS["queue"])
    if version == 0:
        schema[0] = schema[0].replace(",\n            attempt_token TEXT NOT NULL DEFAULT ''", "")
    with sqlite3.connect(data / "graph-sync.sqlite3") as db:
        for ddl in schema:
            db.execute(ddl)
        if version:
            db.execute(f"PRAGMA application_id={v1.IDENTITIES['queue']}")
            db.execute(f"PRAGMA user_version={version}")
        receipt = '{"phase":"sending_mail","synthetic_evidence":"preserve"}'
        db.execute("INSERT INTO graph_sync_jobs(id,kind,payload_json,status,attempts,next_attempt_at,created_at,updated_at,result_json) VALUES(?,?,?,?,?,?,?,?,?)",
                   ("old-job", "archive_and_mail_local", "{}", "running", 1, 0, 1, 1, receipt))
        db.execute("INSERT INTO graph_sync_jobs(id,kind,payload_json,status,attempts,next_attempt_at,created_at,updated_at,result_json) VALUES(?,?,?,?,?,?,?,?,?)",
                   ("done", "notify_teams", "{}", "complete", 1, 0, 1, 1, '{"accepted":true}'))
        if version == 2:
            db.execute("INSERT INTO graph_mail_messages VALUES(?,?,?,?,?,?,?,?,?)",
                       ("old-job", b"synthetic MIME", "synthetic", "synthetic", "synthetic", "abc", 14, "attempt", None))
    db.close()  # sqlite's transaction context does not close the writer handle.
    work = tmp_path / "work"
    assert prepare(data, [], work, source_sha256="a" * 64)["jobs_quarantined"] == 1
    apply(work, source_sha256="a" * 64)
    queue = GraphSyncQueue(None, data / "graph-sync.sqlite3", auto_start=False)
    queue.run_until_idle()
    job = queue.status("old-job")
    assert job["status"] == "failed" and not job["will_retry"]
    assert job["result"]["previous_result_json"] == receipt
    assert job["attempts"] == 1
    with pytest.raises(ValueError):
        queue.retry("old-job")
    assert queue.status("done")["result"] == {"accepted": True}
    if version == 2:
        with closing(sqlite3.connect(data / "graph-sync.sqlite3")) as db:
            assert db.execute("SELECT payload FROM graph_mail_messages").fetchone()[0] == b"synthetic MIME"


def modern_storage(root):
    from src.services.runtime_contract import RuntimeContract
    from src.services.sqlite_storage_format import initialize_store
    from tests.legacy_editing_shapes import version_two
    data = root / "data"
    RuntimeContract(data, "b" * 64).initialize_empty()
    (data / "editing-state").mkdir()
    with sqlite3.connect(data / "editing-state/editing-state.sqlite3") as db:
        initialize_store(db, "editing")
        db.execute("INSERT INTO editing_states VALUES(?,?,?)", ("document", json.dumps(version_two("document")), 1))
    db.close()
    return data, root / "work"


@pytest.mark.parametrize("boundary", ["editing-state.sqlite3", "graph-sync.sqlite3", "runtime-contract.sqlite3"])
def test_interrupted_publication_resumes_and_does_not_admit_partial_data(tmp_path, monkeypatch, boundary):
    import src.services.runtime_migration as migration
    from src.services.runtime_contract import RuntimeContract, ContractError
    data, work = modern_storage(tmp_path)
    migration.prepare(data, [], work, source_sha256="a" * 64)
    original = migration._publish
    def interrupt(source, target, work_id):
        original(source, target, work_id)
        if target.name == boundary:
            raise OSError("Synthetic interruption after publication")
    monkeypatch.setattr(migration, "_publish", interrupt)
    with pytest.raises(OSError, match="Synthetic"):
        migration.apply(work, source_sha256="a" * 64)
    if boundary != "runtime-contract.sqlite3":
        for release in ("a", "b"):
            with pytest.raises(ContractError):
                with RuntimeContract(data, release * 64).operation():
                    pytest.fail("Partial migration admitted an application writer")
    monkeypatch.setattr(migration, "_publish", original)
    assert migration.apply(work, source_sha256="a" * 64)["state"] == "complete"
    with RuntimeContract(data, "a" * 64).operation():
        pass


@pytest.mark.parametrize("change", ["file", "database", "candidate", "contract"])
def test_changes_since_preparation_refuse_publication(tmp_path, change):
    from src.services.runtime_migration import prepare, apply, MigrationError
    data, work = modern_storage(tmp_path)
    file = data / "synthetic-photo.jpg"
    file.write_bytes(b"before")
    prepare(data, [], work, source_sha256="a" * 64)
    if change == "file":
        file.write_bytes(b"after")
    elif change == "database":
        with sqlite3.connect(data / "editing-state/editing-state.sqlite3") as db:
            db.execute("UPDATE editing_states SET updated_at=123")
        db.close()
    elif change == "candidate":
        plan = json.loads((work / "plan.json").read_text())
        (work / plan["attempt"] / "prepared/runtime-contract.sqlite3").write_bytes(b"broken")
    else:
        with sqlite3.connect(data / "runtime-contract.sqlite3") as db:
            db.execute("UPDATE state_contract SET epoch=2")
        db.close()
    with pytest.raises(MigrationError):
        apply(work, source_sha256="a" * 64)
    assert not (data / "graph-sync.sqlite3").exists()


def test_invalid_editing_state_is_preserved_in_quarantine(tmp_path):
    from src.services.runtime_migration import prepare, apply
    from src.services.editing_state_format import EditingStateQuarantinedError
    from src.services.editing_state_service import EditingStateService
    data, work = modern_storage(tmp_path)
    raw = '{"version":99,"document_id":"document","synthetic_future":"keep"}'
    with sqlite3.connect(data / "editing-state/editing-state.sqlite3") as db:
        db.execute("UPDATE editing_states SET state_json=?", (raw,))
    db.close()
    assert prepare(data, [], work, source_sha256="a" * 64)["editing_quarantined"] == 1
    apply(work, source_sha256="a" * 64)
    editing = EditingStateService(data / "editing-state")
    with pytest.raises(EditingStateQuarantinedError):
        editing.snapshot_document("document")
    with closing(editing.repository._connect()) as db:
        assert db.execute("SELECT state_json FROM editing_states").fetchone()[0] == raw


def test_unknown_sqlite_schema_refuses_without_modifying_original(tmp_path):
    from src.services.runtime_migration import prepare, MigrationError
    from src.services.sqlite_storage_format import StorageFormatError
    data = tmp_path / "data"
    data.mkdir()
    path = data / "graph-sync.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE unfamiliar(value TEXT)")
        db.execute("INSERT INTO unfamiliar VALUES('synthetic')")
    db.close()
    before = path.read_bytes()
    with pytest.raises((MigrationError, StorageFormatError), match="desconhecid"):
        prepare(data, [], tmp_path / "work", source_sha256="a" * 64)
    assert path.read_bytes() == before
    assert not (data / "runtime-contract.sqlite3").exists()


def test_prepare_cannot_run_during_admitted_writer(tmp_path):
    from src.services.runtime_migration import prepare
    from src.services.runtime_contract import RuntimeContract, ContractBusy
    data, work = modern_storage(tmp_path)
    with RuntimeContract(data, "b" * 64).operation():
        with pytest.raises(ContractBusy):
            prepare(data, [], work, source_sha256="a" * 64)
    assert not (work / "plan.json").exists()


def test_committed_wal_rows_are_included_in_the_original_snapshot(tmp_path):
    from src.services.runtime_migration import prepare, apply
    from src.services.sqlite_storage_format import SCHEMAS
    from tests.legacy_editing_shapes import version_two
    data = tmp_path / "data"
    editing = data / "editing-state"
    editing.mkdir(parents=True)
    path = editing / "editing-state.sqlite3"
    writer = sqlite3.connect(path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute(SCHEMAS["editing"][0])
    writer.commit()
    reader = sqlite3.connect(path)
    reader.execute("BEGIN")
    reader.execute("SELECT * FROM editing_states").fetchall()
    writer.execute("INSERT INTO editing_states VALUES(?,?,?)", ("wal-doc", json.dumps(version_two("wal-doc")), 1))
    writer.commit()
    writer.close()
    assert Path(str(path) + "-wal").exists()
    work = tmp_path / "work"
    try:
        assert prepare(data, [], work, source_sha256="a" * 64)["editing_converted"] == 1
    finally:
        reader.close()
    apply(work, source_sha256="a" * 64)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT document_id FROM editing_states").fetchone()[0] == "wal-doc"
    plan = json.loads((work / "plan.json").read_text())
    with closing(sqlite3.connect(work / plan["attempt"] / "original/0/editing-state/editing-state.sqlite3")) as db:
        assert db.execute("SELECT document_id FROM editing_states").fetchone()[0] == "wal-doc"


def test_original_snapshot_damage_prevents_any_publication(tmp_path):
    from src.services.runtime_migration import prepare, apply, MigrationError
    data, work = modern_storage(tmp_path)
    (data / "synthetic.xlsx").write_bytes(b"original")
    prepare(data, [], work, source_sha256="a" * 64)
    plan = json.loads((work / "plan.json").read_text())
    (work / plan["attempt"] / "original/0/synthetic.xlsx").write_bytes(b"changed")
    with pytest.raises(MigrationError, match="original"):
        apply(work, source_sha256="a" * 64)
    assert (data / "synthetic.xlsx").read_bytes() == b"original"


def test_two_migration_workspaces_cannot_publish_concurrently(tmp_path, monkeypatch):
    import src.services.runtime_migration as migration
    from src.services.file_mutex import FileMutexBusy
    data, first = modern_storage(tmp_path)
    second = tmp_path / "second"
    for work in (first, second):
        migration.prepare(data, [], work, source_sha256="a" * 64)
    original = migration._publish
    checks = []
    def publish(source, target, work_id):
        with pytest.raises(FileMutexBusy):
            migration.apply(second, source_sha256="a" * 64)
        checks.append(target.name)
        original(source, target, work_id)
    monkeypatch.setattr(migration, "_publish", publish)
    assert migration.apply(first, source_sha256="a" * 64)["state"] == "complete"
    assert "runtime-contract.sqlite3" in checks


def test_resume_after_final_contract_with_changed_format_and_epoch(tmp_path, monkeypatch):
    import src.services.runtime_migration as migration
    from src.services.runtime_contract import SUPPORTED_FORMATS
    data, work = modern_storage(tmp_path)
    with sqlite3.connect(data / "runtime-contract.sqlite3") as db:
        db.execute("UPDATE state_contract SET formats_json=?,epoch=7",
                   (json.dumps({**SUPPORTED_FORMATS, "queue": 1}),))
    db.close()
    migration.prepare(data, [], work, source_sha256="a" * 64)
    original = migration._publish
    def interrupt(source, target, work_id):
        original(source, target, work_id)
        if target.name == "runtime-contract.sqlite3":
            raise OSError("Synthetic last reply lost")
    monkeypatch.setattr(migration, "_publish", interrupt)
    with pytest.raises(OSError):
        migration.apply(work, source_sha256="a" * 64)
    monkeypatch.setattr(migration, "_publish", original)
    assert migration.apply(work, source_sha256="a" * 64)["state"] == "complete"


def test_cli_requires_verified_artifact_and_explicit_offline_inventory(tmp_path, monkeypatch, capsys):
    from tools import migrate_runtime as cli
    from src.services.release_identity import write_release_manifest
    artifact = tmp_path / "artifact"
    (artifact / "src").mkdir(parents=True)
    (artifact / "src/entry.py").write_text("synthetic = True\n")
    monkeypatch.setattr(cli, "ROOT", artifact)
    data = tmp_path / "data"
    data.mkdir()
    args = ["prepare", "--data-dir", str(data), "--workspace", str(tmp_path / "work"),
            "--writers-stopped", "--roots-complete"]
    assert cli.main(args) == 2
    assert "manifesto" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(args[:-1])
    write_release_manifest(artifact, revision="a" * 40)
    assert cli.main(args) == 0
    assert cli.main(["apply", "--workspace", str(tmp_path / "work"), "--writers-stopped"]) == 0


def test_preparation_restart_reuses_no_partial_candidate(tmp_path, monkeypatch):
    import src.services.runtime_migration as migration
    data, work = modern_storage(tmp_path)
    original = migration._convert_queue
    monkeypatch.setattr(migration, "_convert_queue", lambda *_: (_ for _ in ()).throw(OSError("Synthetic stop")))
    with pytest.raises(OSError):
        migration.prepare(data, [], work, source_sha256="a" * 64)
    assert not (work / "plan.json").exists()
    partials = [p for p in work.iterdir() if p.is_dir()]
    monkeypatch.setattr(migration, "_convert_queue", original)
    migration.prepare(data, [], work, source_sha256="a" * 64)
    assert all(p.exists() for p in partials)
    assert migration.apply(work, source_sha256="a" * 64)["state"] == "complete"


@pytest.mark.parametrize("field", ["sadi", "queue"])
def test_future_runtime_format_is_not_downgraded_by_migration(tmp_path, field):
    from src.services.runtime_migration import prepare, MigrationError
    from src.services.runtime_contract import SUPPORTED_FORMATS, RuntimeContract
    data, work = modern_storage(tmp_path)
    with sqlite3.connect(data / "runtime-contract.sqlite3") as db:
        db.execute("UPDATE state_contract SET formats_json=?", (json.dumps({**SUPPORTED_FORMATS, field: 99}),))
    db.close()
    with pytest.raises(MigrationError, match="Contrato persistente desconhecido"):
        prepare(data, [], work, source_sha256="a" * 64)
    assert RuntimeContract(data, "b" * 64).inspect()["formats"][field] == 99
