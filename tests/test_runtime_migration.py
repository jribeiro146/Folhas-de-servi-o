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
    assert job["reconciliation_required"] is True
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
@pytest.mark.parametrize("has_contract", [True, False])
def test_interrupted_publication_resumes_and_does_not_admit_partial_data(tmp_path, monkeypatch, boundary, has_contract):
    import src.services.runtime_migration as migration
    from src.services.runtime_contract import RuntimeContract, ContractError
    data, work = modern_storage(tmp_path)
    if not has_contract:
        (data / "runtime-contract.sqlite3").unlink()
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


def test_historical_session_without_last_seen_is_converted_without_extending_it(tmp_path):
    from src.services.runtime_migration import prepare, apply
    from tests.legacy_editing_shapes import version_two
    from tests.legacy_editing_sessions_a1 import HistoricalEditingStateService
    data, work = modern_storage(tmp_path)
    state = version_two("document")
    lease = legacy_lease("document")["lease"]
    state["lease"] = lease
    HistoricalEditingStateService()._normalize_sessions(state, now=1)
    assert "last_seen_at" not in state["sessions"][lease["token"]]
    with sqlite3.connect(data / "editing-state/editing-state.sqlite3") as db:
        db.execute("UPDATE editing_states SET state_json=?", (json.dumps(state),))
    db.close()
    assert prepare(data, [], work, source_sha256="a" * 64)["editing_quarantined"] == 0
    apply(work, source_sha256="a" * 64)
    with closing(sqlite3.connect(data / "editing-state/editing-state.sqlite3")) as db:
        result = json.loads(db.execute("SELECT state_json FROM editing_states").fetchone()[0])
    assert result["sessions"][lease["token"]] == {**lease, "last_seen_at": 1}


def test_publication_preserves_owner_and_mode_before_replacing_each_database(tmp_path, monkeypatch):
    import src.services.runtime_migration as migration
    import os
    import stat
    data, work = modern_storage(tmp_path)
    migration.prepare(data, [], work, source_sha256="a" * 64)
    targets = {data / "editing-state/editing-state.sqlite3", data / "runtime-contract.sqlite3", data / "graph-sync.sqlite3"}
    original_stat = Path.stat
    original_chmod = os.chmod
    chowns, chmods = [], []
    def owned(path, *args, **kwargs):
        value = original_stat(path, *args, **kwargs)
        if path in targets or path == data:
            fields = list(value)
            fields[0] = (stat.S_IFDIR | 0o700) if path == data else (stat.S_IFREG | 0o600)
            fields[4], fields[5] = 10001, 10002
            return os.stat_result(fields)
        return value
    def chmod(path, mode, *args, **kwargs):
        chmods.append((Path(path), mode))
        return original_chmod(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, "stat", owned)
    monkeypatch.setattr(os, "chown", lambda path, uid, gid: chowns.append((Path(path), uid, gid)), raising=False)
    monkeypatch.setattr(os, "chmod", chmod)
    migration.apply(work, source_sha256="a" * 64)
    for target in targets:
        temporary_name = target.name + "." + json.loads((work / "plan.json").read_text())["attempt"] + ".migration"
        temporary = target.with_name(temporary_name)
        assert (temporary, 10001, 10002) in chowns
        assert (temporary, 0o600) in chmods


@pytest.mark.skipif(__import__("os").name != "posix", reason="Real POSIX ownership/modes require Linux")
def test_posix_database_permissions_are_preserved(tmp_path):
    from src.services.runtime_migration import prepare, apply
    import stat
    data, work = modern_storage(tmp_path)
    targets = [data / "editing-state/editing-state.sqlite3", data / "runtime-contract.sqlite3"]
    for target in targets:
        target.chmod(0o600)
    before = {target: (target.stat().st_uid, target.stat().st_gid) for target in targets}
    prepare(data, [], work, source_sha256="a" * 64)
    apply(work, source_sha256="a" * 64)
    for target in targets:
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert (target.stat().st_uid, target.stat().st_gid) == before[target]


def test_cli_malformed_snapshot_returns_structured_refusal(tmp_path, monkeypatch, capsys):
    from tools import migrate_runtime as cli
    from src.services.release_identity import write_release_manifest
    artifact = tmp_path / "artifact"
    (artifact / "src").mkdir(parents=True)
    (artifact / "src/entry.py").write_text("synthetic = True\n")
    write_release_manifest(artifact, revision="a" * 40)
    monkeypatch.setattr(cli, "ROOT", artifact)
    data = tmp_path / "data"
    (data / "editing-state").mkdir(parents=True)
    raw = b'{"broken"'
    snapshot = data / "editing-state/broken.json"
    snapshot.write_bytes(raw)
    assert cli.main(["prepare", "--data-dir", str(data), "--workspace", str(tmp_path / "work"),
                     "--writers-stopped", "--roots-complete"]) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "migration_refused"
    assert snapshot.read_bytes() == raw
    assert not (tmp_path / "work/plan.json").exists()


def test_historical_sqlite_import_takes_precedence_over_old_json(tmp_path):
    from tests.legacy_editing_repository_a1 import SqliteStateRepository
    from src.services.runtime_migration import prepare, apply
    from tests.legacy_editing_shapes import version_two
    data = tmp_path / "data"
    editing = data / "editing-state"
    editing.mkdir(parents=True)
    snapshot = editing / snapshot_name("historical")
    snapshot.write_text(json.dumps(legacy_lease("historical")))
    repository = SqliteStateRepository(editing / "editing-state.sqlite3", legacy_root=editing)
    with repository.transaction("historical", lambda: {}) as state:
        state.clear()
        state.update(version_two("historical"), revision=12)
    # The historical implementation's connection context commits but does not
    # close. Collect unreachable handles to model stopped writers on Windows.
    import gc
    gc.collect()
    work = tmp_path / "work"
    assert prepare(data, [], work, source_sha256="a" * 64)["snapshots_shadowed"] == 1
    apply(work, source_sha256="a" * 64)
    with closing(sqlite3.connect(editing / "editing-state.sqlite3")) as db:
        assert json.loads(db.execute("SELECT state_json FROM editing_states").fetchone()[0])["revision"] == 12
    assert json.loads(snapshot.read_text())["version"] == 1


def test_failure_to_preserve_ownership_refuses_replacement_and_can_resume(tmp_path, monkeypatch):
    import src.services.runtime_migration as migration
    import os
    from src.services.runtime_contract import RuntimeContract, ContractError
    data, work = modern_storage(tmp_path)
    migration.prepare(data, [], work, source_sha256="a" * 64)
    source = data / "editing-state/editing-state.sqlite3"
    before = source.read_bytes()
    def denied(*_args):
        raise PermissionError("Synthetic ownership refusal")
    monkeypatch.setattr(os, "chown", denied, raising=False)
    with pytest.raises(PermissionError):
        migration.apply(work, source_sha256="a" * 64)
    assert source.read_bytes() == before
    with pytest.raises(ContractError):
        with RuntimeContract(data, "a" * 64).operation():
            pytest.fail("Migration with incomplete publication was admitted")
    monkeypatch.setattr(os, "chown", lambda *_: None)
    assert migration.apply(work, source_sha256="a" * 64)["state"] == "complete"


@pytest.mark.parametrize("kind,evidence", [("remove_active", {"cancellation_started": True}),
                                          ("notify_teams", {"phase": "sending_teams", "accepted": False})])
def test_legacy_cancel_and_teams_keep_visible_uncertainty(tmp_path, kind, evidence):
    from src.services.runtime_migration import prepare, apply
    from src.services.graph_sync_queue import GraphSyncQueue
    from tests.legacy_sqlite_format_v2 import SCHEMAS, IDENTITIES
    data = tmp_path / "data"
    data.mkdir()
    raw = json.dumps(evidence)
    with sqlite3.connect(data / "graph-sync.sqlite3") as db:
        for statement in SCHEMAS["queue"]:
            db.execute(statement)
        db.execute(f"PRAGMA application_id={IDENTITIES['queue']}")
        db.execute("PRAGMA user_version=2")
        db.execute("INSERT INTO graph_sync_jobs(id,kind,payload_json,status,attempts,next_attempt_at,created_at,updated_at,result_json) VALUES(?,?,?,?,?,?,?,?,?)",
                   ("uncertain", kind, "{}", "running", 1, 0, 1, 1, raw))
    db.close()
    work = tmp_path / "work"
    prepare(data, [], work, source_sha256="a" * 64)
    apply(work, source_sha256="a" * 64)
    queue = GraphSyncQueue(None, data / "graph-sync.sqlite3", auto_start=False)
    queue.run_until_idle()
    result = queue.status("uncertain")
    assert result["reconciliation_required"] is True and not result["will_retry"]
    assert result["result"]["previous_result_json"] == raw
    with pytest.raises(ValueError):
        queue.retry("uncertain")


@pytest.mark.parametrize("kind,evidence", [
    ("upload_active", {"phase": ["sending_mail"]}),
    ("archive_and_mail_local", {"phase": {}, "mail": "accepted"}),
    ("notify_teams", {"accepted": "yes", "retry_not_before": -1}),
    ("notify_teams", {"retry_not_before": float("inf")}),
    ("archive_and_mail_local", {"phase": "sending_mail", "mail": {"accepted": "yes"}}),
    ("remove_active", {"cancellation_started": "yes"}),
])
def test_corrupt_legacy_results_stay_readable_and_block_repetition(tmp_path, kind, evidence):
    from src.services.runtime_migration import prepare, apply
    from src.services.graph_sync_queue import GraphSyncQueue
    from tests.legacy_sqlite_format_v2 import SCHEMAS, IDENTITIES
    data = tmp_path / "data"
    data.mkdir()
    draft = tmp_path / "synthetic.xlsx"
    raw = json.dumps(evidence)
    with sqlite3.connect(data / "graph-sync.sqlite3") as db:
        for statement in SCHEMAS["queue"]:
            db.execute(statement)
        db.execute(f"PRAGMA application_id={IDENTITIES['queue']}")
        db.execute("PRAGMA user_version=2")
        db.execute("INSERT INTO graph_sync_jobs(id,kind,payload_json,status,attempts,next_attempt_at,created_at,updated_at,result_json) VALUES(?,?,?,?,?,?,?,?,?)",
                   ("corrupt", kind, json.dumps({"draft_path": str(draft)}), "running", 1, 0, 1, 1, raw))
    db.close()
    work = tmp_path / "work"
    prepare(data, [], work, source_sha256="a" * 64)
    apply(work, source_sha256="a" * 64)
    queue = GraphSyncQueue(None, data / "graph-sync.sqlite3", auto_start=False)
    queue.run_until_idle()
    result = queue.status("corrupt")
    assert result["reconciliation_required"] is True and not result["will_retry"]
    assert result["result"]["previous_result_json"] == raw
    with pytest.raises(ValueError):
        queue.retry("corrupt")
    if kind == "upload_active":
        assert queue.unfinished_upload(draft)["status"] == "failed"
        assert queue.has_unfinished_upload(draft)
