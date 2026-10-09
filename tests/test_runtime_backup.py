"""Backup/recovery public operations on synthetic, offline storage."""
from contextlib import closing
import gc
import json
from pathlib import Path
import sqlite3

import pytest

from src.services.runtime_contract import RuntimeContract, ContractError, SUPPORTED_FORMATS
from src.services.sqlite_storage_format import initialize_store

SOURCE = "a" * 64
KEY = b"synthetic-backup-key-for-tests-only-2026"


def storage(tmp_path):
    from src.services.graph_sync_queue import GraphSyncQueue
    data = tmp_path / "data"
    RuntimeContract(data, SOURCE).initialize_empty()
    excel = tmp_path / "Excel"
    excel.mkdir()
    (excel / "draft.xlsx").write_bytes(b"synthetic workbook")
    (excel / "signature.png").write_bytes(b"synthetic signature")
    (data / "sadi").mkdir()
    (data / "sadi" / "private.json").write_bytes(b'{"synthetic":true}')
    # Create the real empty queue's ancillary directories/worker lock before the
    # snapshot, as a normally used runtime would. No jobs or transports yet.
    GraphSyncQueue(None, data / "graph-sync.sqlite3", auto_start=False).run_until_idle()
    with closing(sqlite3.connect(data / "graph-sync.sqlite3")) as db:
        initialize_store(db, "queue")
        db.execute("INSERT INTO graph_sync_jobs(id,kind,payload_json,status,attempts,next_attempt_at,created_at,updated_at,result_json) VALUES(?,?,?,?,?,?,?,?,?)",
                   ("mail", "archive_and_mail_local", "{}", "pending", 0, 0, 1, 1, "{}"))
        db.execute("INSERT INTO graph_sync_jobs(id,kind,payload_json,status,attempts,next_attempt_at,created_at,updated_at,result_json) VALUES(?,?,?,?,?,?,?,?,?)",
                   ("teams", "notify_teams", "{}", "complete", 1, 0, 1, 1, '{"accepted":true}'))
        db.execute("INSERT INTO graph_mail_messages VALUES(?,?,?,?,?,?,?,?,?)",
                   ("mail", b"synthetic MIME", "synthetic", "synthetic", "synthetic", "abc", 14, "attempt", None))
        db.commit()
    return data, excel, tmp_path / "backup", tmp_path / "restore"


def test_backup_covers_committed_wal_and_all_private_files_without_secret(tmp_path):
    from src.services.runtime_backup import backup, verify
    data, excel, destination, _ = storage(tmp_path)
    with closing(sqlite3.connect(data / "graph-sync.sqlite3")) as reader:
        reader.execute("PRAGMA journal_mode=WAL")
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM graph_sync_jobs").fetchall()
        with closing(sqlite3.connect(data / "graph-sync.sqlite3")) as writer:
            writer.execute("UPDATE graph_sync_jobs SET attempts=2 WHERE id='mail'")
            writer.commit()
        report = backup(data, [excel], destination, source_sha256=SOURCE, key=KEY)
        assert report["state"] == "verified"
        assert verify(destination, key=KEY)["snapshot_id"] == report["snapshot_id"]
        with closing(sqlite3.connect(destination / "files/0/graph-sync.sqlite3")) as copy:
            assert copy.execute("SELECT attempts FROM graph_sync_jobs WHERE id='mail'").fetchone() == (2,)
            assert copy.execute("SELECT payload FROM graph_mail_messages").fetchone() == (b"synthetic MIME",)
    assert (destination / "files/1/signature.png").read_bytes() == b"synthetic signature"
    assert (destination / "files/0/sadi/private.json").read_bytes() == b'{"synthetic":true}'
    assert KEY not in (destination / "backup.json").read_bytes()
    assert RuntimeContract(data, SOURCE).inspect()["matches_release"]


def retire_roots(tmp_path, *roots):
    # Only synthetic roots owned by this test may be moved aside for recovery.
    # Emulate process shutdown, including SQLite handles awaiting collection in
    # the existing queue. Recovery itself never forces another process to close.
    gc.collect()
    for root in roots:
        assert root.resolve().is_relative_to(tmp_path.resolve())
        root.rename(root.with_name(root.name + "-retired"))


def test_restore_before_external_acceptance_blocks_old_and_new_operations(tmp_path):
    from src.services.runtime_backup import backup, restore, release_hold, BackupError
    from src.services.graph_sync_queue import GraphSyncQueue
    data, excel, destination, workspace = storage(tmp_path)
    backup(data, [excel], destination, source_sha256=SOURCE, key=KEY)
    # An email accepted AFTER the snapshot, including a job absent from it.
    external_receipts = {"mail": "accepted", "new-job-after-backup": "accepted"}
    retire_roots(tmp_path, data, excel)
    result = restore(destination, workspace, source_sha256=SOURCE, key=KEY)
    assert result["state"] == "held"
    for source in (SOURCE, "0" * 64):
        contract = RuntimeContract(data, source)
        with pytest.raises(ContractError):
            with contract.operation():
                pytest.fail("Restored storage admitted a new application effect")
        with pytest.raises(ContractError):
            contract.activate(SOURCE, next_formats=SUPPORTED_FORMATS)
    queue = GraphSyncQueue(None, data / "graph-sync.sqlite3", auto_start=False)
    queue.run_until_idle()
    assert queue.status("mail")["reconciliation_required"]
    assert not queue.status("mail")["will_retry"]
    assert queue.status("teams")["result"] == {"accepted": True}
    with pytest.raises(ValueError):
        queue.retry("mail")
    assert external_receipts == {"mail": "accepted", "new-job-after-backup": "accepted"}
    gc.collect()
    with pytest.raises(BackupError):
        release_hold(workspace, source_sha256=SOURCE, key=KEY, actor="installer", reason="checked",
                     external_effects_reconciled=False)
    assert release_hold(workspace, source_sha256=SOURCE, key=KEY, actor="installer",
                        reason="Synthetic check covers all effects after backup, including absent jobs",
                        external_effects_reconciled=True)["state"] == "released"
    with RuntimeContract(data, SOURCE).operation():
        pass
    # Deliberate global release does not authorize replay of restored queue jobs.
    assert not queue.status("mail")["will_retry"]
    with pytest.raises(ValueError):
        queue.retry("mail")
    assert (excel / "signature.png").read_bytes() == b"synthetic signature"
    assert (data / "sadi/private.json").read_bytes() == b'{"synthetic":true}'
    audit = json.loads((workspace / "reconciliation.json").read_text())
    assert audit["actor"] == "installer" and audit["external_effects_reconciled"] is True


@pytest.mark.parametrize("boundary", ["runtime-contract.sqlite3", "graph-sync.sqlite3", "signature.png"])
def test_interrupted_restore_resumes_under_global_hold(tmp_path, monkeypatch, boundary):
    import src.services.runtime_backup as recovery
    data, excel, destination, workspace = storage(tmp_path)
    recovery.backup(data, [excel], destination, source_sha256=SOURCE, key=KEY)
    retire_roots(tmp_path, data, excel)
    publish = recovery._publish_recovery
    def interrupted(source, target, metadata, snapshot_id):
        publish(source, target, metadata, snapshot_id)
        if target.name == boundary:
            raise OSError("Synthetic stop after atomic publication")
    monkeypatch.setattr(recovery, "_publish_recovery", interrupted)
    with pytest.raises(OSError, match="Synthetic stop"):
        recovery.restore(destination, workspace, source_sha256=SOURCE, key=KEY)
    with pytest.raises(ContractError):
        with RuntimeContract(data, SOURCE).operation():
            pytest.fail("Partial recovery was admitted")
    monkeypatch.setattr(recovery, "_publish_recovery", publish)
    assert recovery.restore(destination, workspace, source_sha256=SOURCE, key=KEY)["state"] == "held"
    assert recovery.restore(destination, workspace, source_sha256=SOURCE, key=KEY)["state"] == "held"
    assert recovery.verify(destination, key=KEY)["state"] == "verified"


@pytest.mark.parametrize("damage", ["key", "file", "database", "nonempty"])
def test_recovery_refuses_before_writing_targets(tmp_path, damage):
    from src.services.runtime_backup import backup, restore, BackupError
    data, excel, destination, workspace = storage(tmp_path)
    backup(data, [excel], destination, source_sha256=SOURCE, key=KEY)
    retire_roots(tmp_path, data, excel)
    key = KEY
    if damage == "key":
        key = b"different-synthetic-key-not-compatible"
    elif damage == "file":
        (destination / "files/1/signature.png").write_bytes(b"changed")
    elif damage == "database":
        with closing(sqlite3.connect(destination / "files/0/graph-sync.sqlite3")) as db:
            db.execute("DELETE FROM graph_sync_jobs")
            db.commit()
    else:
        excel.mkdir()
        (excel / "preserve.txt").write_text("existing")
    with pytest.raises(BackupError):
        restore(destination, workspace, source_sha256=SOURCE, key=key)
    assert not data.exists()
    if damage == "nonempty":
        assert (excel / "preserve.txt").read_text() == "existing"


def test_existing_writer_and_recovery_hold_refuse_backup(tmp_path):
    from src.services.runtime_backup import backup, restore, BackupError
    data, excel, destination, workspace = storage(tmp_path)
    with RuntimeContract(data, SOURCE).operation():
        with pytest.raises(ContractError):
            backup(data, [excel], destination, source_sha256=SOURCE, key=KEY)
    other = tmp_path / "second-backup"
    backup(data, [excel], other, source_sha256=SOURCE, key=KEY)
    retire_roots(tmp_path, data, excel)
    restore(other, workspace, source_sha256=SOURCE, key=KEY)
    with pytest.raises(BackupError):
        backup(data, [excel], tmp_path / "held-backup", source_sha256=SOURCE, key=KEY)


@pytest.mark.parametrize("change", ["modified", "missing", "extra"])
def test_resume_preserves_unexpected_destination_changes(tmp_path, monkeypatch, change):
    import src.services.runtime_backup as recovery
    data, excel, destination, workspace = storage(tmp_path)
    recovery.backup(data, [excel], destination, source_sha256=SOURCE, key=KEY)
    retire_roots(tmp_path, data, excel)
    recovery.restore(destination, workspace, source_sha256=SOURCE, key=KEY)
    if change == "modified":
        (excel / "draft.xlsx").write_bytes(b"unexpected edits")
    elif change == "missing":
        (excel / "draft.xlsx").unlink()
    else:
        (excel / "extra.xlsx").write_bytes(b"unexpected document")
    with pytest.raises(recovery.BackupError):
        recovery.release_hold(workspace, source_sha256=SOURCE, key=KEY, actor="installer",
                              reason="Synthetic external reconciliation", external_effects_reconciled=True)
    with pytest.raises(recovery.BackupError):
        recovery.restore(destination, workspace, source_sha256=SOURCE, key=KEY)
    if change == "modified":
        assert (excel / "draft.xlsx").read_bytes() == b"unexpected edits"


def test_restore_preserves_file_version_mtime_and_private_posix_access(tmp_path):
    import os
    from src.services.runtime_backup import backup, restore
    data, excel, destination, workspace = storage(tmp_path)
    os.chmod(excel / "signature.png", 0o600)
    os.utime(excel / "signature.png", ns=(1700000000000000000, 1700000000000000000))
    original = (excel / "signature.png").stat()
    backup(data, [excel], destination, source_sha256=SOURCE, key=KEY)
    retire_roots(tmp_path, data, excel)
    restore(destination, workspace, source_sha256=SOURCE, key=KEY)
    restored = (excel / "signature.png").stat()
    assert restored.st_mtime_ns == original.st_mtime_ns
    if os.name != "nt":
        assert (restored.st_mode, restored.st_uid, restored.st_gid) == (original.st_mode, original.st_uid, original.st_gid)


def test_backup_cli_requires_verified_artifact_and_explicit_offline_flags(tmp_path, monkeypatch, capsys):
    from tools import backup_runtime as cli
    data, excel, destination, workspace = storage(tmp_path)
    keyfile = tmp_path / "private-key"
    keyfile.write_bytes(KEY)
    argv = ["backup", "--data-dir", str(data), "--include-root", str(excel), "--backup", str(destination),
            "--key-file", str(keyfile), "--writers-stopped", "--roots-complete"]
    monkeypatch.setattr(cli, "get_release_info", lambda _: {"verified": False, "source_sha256": SOURCE})
    assert cli.main(argv) == 2
    assert not destination.exists()
    monkeypatch.setattr(cli, "get_release_info", lambda _: {"verified": True, "source_sha256": SOURCE})
    assert cli.main(argv) == 0
    with pytest.raises(SystemExit):
        cli.main(argv[:-2])
    retire_roots(tmp_path, data, excel)
    assert cli.main(["restore", "--backup", str(destination), "--workspace", str(workspace),
                     "--key-file", str(keyfile), "--writers-stopped"]) == 0
    assert KEY.decode() not in str(capsys.readouterr())


def test_sadi_signature_remains_valid_with_separately_preserved_key(tmp_path):
    from src import maintenance_schema as sadi
    from tests.test_maintenance import complete_document, sign_document
    from src.services.runtime_backup import backup, restore
    data, excel, destination, workspace = storage(tmp_path)
    document = complete_document()
    sign_document(document, KEY.decode())
    (data / "sadi/private.json").write_text(json.dumps(document), encoding="utf-8")
    backup(data, [excel], destination, source_sha256=SOURCE, key=KEY)
    retire_roots(tmp_path, data, excel)
    restore(destination, workspace, source_sha256=SOURCE, key=KEY)
    recovered = json.loads((data / "sadi/private.json").read_text(encoding="utf-8"))
    assert recovered == document
    for site in recovered["maintenance_checklists"]:
        assert sadi.signature_valid(KEY.decode(), recovered, site, "technician")
        assert not sadi.signature_valid("wrong-synthetic-key", recovered, site, "technician")


@pytest.mark.parametrize("boundary", ["decision", "activated"])
def test_interrupted_explicit_release_retains_audit_and_resumes(tmp_path, monkeypatch, boundary):
    import src.services.runtime_backup as recovery
    data, excel, destination, workspace = storage(tmp_path)
    recovery.backup(data, [excel], destination, source_sha256=SOURCE, key=KEY)
    retire_roots(tmp_path, data, excel)
    recovery.restore(destination, workspace, source_sha256=SOURCE, key=KEY)
    record = recovery._json
    def interrupted(path, value):
        if path.name == "restore.json" and value["state"] == "released" and boundary == "activated":
            raise OSError("Synthetic release interruption")
        record(path, value)
        if path.name == "restore.json" and value["state"] == "releasing" and boundary == "decision":
            raise OSError("Synthetic release interruption")
    monkeypatch.setattr(recovery, "_json", interrupted)
    params = dict(source_sha256=SOURCE, key=KEY, actor="installer", reason="Synthetic external reconciliation",
                  external_effects_reconciled=True)
    with pytest.raises(OSError, match="Synthetic release"):
        recovery.release_hold(workspace, **params)
    audit = (workspace / "reconciliation.json").read_bytes()
    if boundary == "decision":
        with pytest.raises(ContractError):
            with RuntimeContract(data, SOURCE).operation():
                pytest.fail("Release interrupted before activation admitted a writer")
    monkeypatch.setattr(recovery, "_json", record)
    assert recovery.release_hold(workspace, **params)["state"] == "released"
    assert (workspace / "reconciliation.json").read_bytes() == audit
    assert recovery.restore(destination, workspace, source_sha256=SOURCE, key=KEY)["state"] == "released"


def test_real_web_and_worker_factories_refuse_restored_contract(tmp_path, monkeypatch):
    from src.web.application import create_app
    from src.queue_worker import build_queue
    import src.services.runtime_admission as admission
    from src.services.runtime_backup import backup, restore
    data, excel, destination, workspace = storage(tmp_path)
    backup(data, [excel], destination, source_sha256=SOURCE, key=KEY)
    retire_roots(tmp_path, data, excel)
    restore(destination, workspace, source_sha256=SOURCE, key=KEY)
    monkeypatch.setattr(admission, "get_release_info", lambda _: {"verified": True, "source_sha256": SOURCE})
    contract = RuntimeContract(data, SOURCE)
    for factory in (create_app, build_queue):
        with pytest.raises(ContractError, match="Formatos persistentes"):
            factory(runtime_contract=contract)
