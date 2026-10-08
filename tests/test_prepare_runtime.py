"""Run the preparation CLI from a synthetic packaged copy, never an app config."""
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from src.services.release_identity import get_release_info, write_release_manifest
from src.services.runtime_contract import RuntimeContract, ContractError, ContractBusy, SUPPORTED_FORMATS
from src.services.sqlite_storage_format import initialize_store


@pytest.fixture
def package(tmp_path):
    root = tmp_path / "artifact"
    source = Path(__file__).resolve().parents[1]
    for relative in ("src/__init__.py", "src/services/__init__.py", "src/services/release_identity.py",
                     "src/services/runtime_contract.py", "src/services/runtime_admission.py",
                     "src/services/sqlite_storage_format.py", "src/services/file_mutex.py",
                     "tools/prepare_runtime.py"):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, destination)
    return root


def run_cli(package, *args):
    return subprocess.run([sys.executable, "-B", str(package / "tools" / "prepare_runtime.py"), *map(str, args)],
                          capture_output=True, cwd=package.parent, timeout=30)


def test_init_requires_a_verified_artifact_and_preserves_legacy_data(package, tmp_path):
    data = tmp_path / "data"
    result = run_cli(package, "init", "--data-dir", data)
    assert result.returncode == 2
    assert json.loads(result.stderr)["code"] == "state_contract_invalid"
    assert not data.exists()
    write_release_manifest(package, revision="a" * 40)
    data.mkdir()
    evidence = data / "retained.txt"
    evidence.write_text("synthetic recovery evidence")
    result = run_cli(package, "init", "--data-dir", data)
    assert result.returncode != 0
    assert sorted(path.name for path in data.iterdir()) == ["retained.txt"]
    assert evidence.read_text() == "synthetic recovery evidence"


def test_init_inspect_and_repeat_do_not_import_app_or_config(package, tmp_path):
    data = tmp_path / "data"
    info = write_release_manifest(package, revision="a" * 40)
    for _ in range(2):
        result = run_cli(package, "init", "--data-dir", data)
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        response = json.loads(result.stdout)
        assert response["runtime"]["source_sha256"] == info["source_sha256"]
        assert response["runtime"]["epoch"] == 1
    script = '''
import runpy, sys
sys.argv = [sys.argv[1], "inspect", "--data-dir", sys.argv[2]]
try:
    runpy.run_path(sys.argv[0], run_name="__main__")
except SystemExit as exc:
    assert exc.code == 0
assert "src.config" not in sys.modules
assert "src.web.application" not in sys.modules
assert "src.queue_worker" not in sys.modules
'''
    inspected = subprocess.run([sys.executable, "-B", "-c", script,
                                str(package / "tools" / "prepare_runtime.py"), str(data)],
                               capture_output=True, cwd=package.parent, timeout=30)
    assert inspected.returncode == 0, inspected.stderr.decode(errors="replace")
    assert json.loads(inspected.stdout)["release"]["verified"] is True
    assert not (data / "logs").exists()


def test_activation_requires_expected_source_and_does_not_interrupt_operations(package, tmp_path):
    data = tmp_path / "data"
    first = write_release_manifest(package, revision="a" * 40)
    initial = run_cli(package, "init", "--data-dir", data)
    assert initial.returncode == 0, initial.stderr.decode(errors="replace")
    contract = RuntimeContract(data, first["source_sha256"])
    (package / "src" / "synthetic_revision.py").write_text("revision=2\n")
    second = write_release_manifest(package, revision="b" * 40)
    command = ["activate", "--data-dir", data, "--expected-source", first["source_sha256"]]
    assert run_cli(package, *command).returncode != 0  # explicit stopped-writers acknowledgement
    command += ["--writers-stopped"]
    with contract.operation():
        blocked = run_cli(package, *command)
        assert blocked.returncode != 0
    assert contract.inspect()["source_sha256"] == first["source_sha256"]
    activated = run_cli(package, *command)
    assert activated.returncode == 0, activated.stderr.decode(errors="replace")
    state = json.loads(activated.stdout)["runtime"]
    assert state["source_sha256"] == second["source_sha256"]
    assert state["epoch"] == 2
    with pytest.raises(ContractError):
        with contract.operation():
            pytest.fail("Old code admitted after activation")
    assert run_cli(package, *command).returncode != 0  # stale expected source


def test_activation_checks_storage_under_exclusion_and_preserves_version_on_failure(tmp_path):
    contract = RuntimeContract(tmp_path / "data", "a" * 64)
    contract.initialize_empty()
    calls = []

    def validate():
        with pytest.raises(ContractBusy):
            with contract.operation():
                pytest.fail("Validation did not hold exclusion")
        calls.append("checked")
        raise ContractError("Synthetic incompatible storage")
    with pytest.raises(ContractError, match="Synthetic"):
        contract.activate("b" * 64, next_formats=SUPPORTED_FORMATS, validate_storage=validate)
    assert calls == ["checked"]
    assert contract.inspect()["source_sha256"] == "a" * 64
    assert contract.inspect()["epoch"] == 1


def test_invalid_package_or_unsafe_destination_never_creates_data(package, tmp_path):
    write_release_manifest(package, revision="a" * 40)
    inside_code = package / "src" / "data"
    assert run_cli(package, "init", "--data-dir", inside_code).returncode != 0
    assert not inside_code.exists()
    assert run_cli(package, "init", "--data-dir", "relative-data").returncode != 0
    assert not (package.parent / "relative-data").exists()
    (package / "src" / "unexpected.py").write_text("changed=1\n")
    data = tmp_path / "data"
    assert run_cli(package, "init", "--data-dir", data).returncode != 0
    assert not data.exists()


@pytest.mark.parametrize("kind", ["editing", "queue"])
def test_incompatible_database_prevents_activation(package, tmp_path, kind):
    data = tmp_path / "data"
    first = write_release_manifest(package, revision="a" * 40)
    assert run_cli(package, "init", "--data-dir", data).returncode == 0
    path = data / ("editing-state/editing-state.sqlite3" if kind == "editing" else "graph-sync.sqlite3")
    path.parent.mkdir(exist_ok=True)
    db = sqlite3.connect(path)
    try:
        initialize_store(db, kind)
        db.execute("PRAGMA user_version=99")
        db.commit()
    finally:
        db.close()
    before = path.read_bytes()
    (package / "src" / "revision.py").write_text("revision=2\n")
    write_release_manifest(package, revision="b" * 40)
    result = run_cli(package, "activate", "--data-dir", data, "--expected-source", first["source_sha256"],
                     "--writers-stopped")
    assert result.returncode == 2
    assert json.loads(result.stderr)["code"] == "storage_format_invalid"
    assert RuntimeContract(data, first["source_sha256"]).inspect()["epoch"] == 1
    assert path.read_bytes() == before


def test_inspect_reports_other_release_and_future_formats_without_adopting_them(package, tmp_path):
    data = tmp_path / "data"
    first = write_release_manifest(package, revision="a" * 40)
    assert run_cli(package, "init", "--data-dir", data).returncode == 0
    contract = RuntimeContract(data, first["source_sha256"])
    db = sqlite3.connect(contract.path)
    try:
        db.execute("UPDATE state_contract SET formats_json=?", (json.dumps({"editing": 99}),))
        db.commit()
    finally:
        db.close()
    (package / "src" / "revision.py").write_text("revision=2\n")
    write_release_manifest(package, revision="b" * 40)
    result = run_cli(package, "inspect", "--data-dir", data)
    assert result.returncode == 0
    state = json.loads(result.stdout)["runtime"]
    assert state["source_sha256"] == first["source_sha256"]
    assert state["matches_release"] is False
    assert state["formats_compatible"] is False
    assert run_cli(package, "activate", "--data-dir", data, "--expected-source", first["source_sha256"],
                   "--writers-stopped").returncode == 2
    assert contract.inspect() == {**state, "matches_release": True}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows UNC resolution")
@pytest.mark.parametrize("destination", [r"\\synthetic.invalid\share\data", r"\\?\UNC\synthetic.invalid\share\data"])
def test_local_alias_resolving_to_unc_is_rejected_before_io(monkeypatch, destination):
    from tools.prepare_runtime import _data_path
    remote = Path(destination)
    monkeypatch.setattr(Path, "resolve", lambda self: remote)
    with pytest.raises(ValueError, match="local"):
        _data_path(r"C:\synthetic-alias\data")


def test_activation_of_existing_stores_and_invalid_expected_source(package, tmp_path):
    data = tmp_path / "data"
    first = write_release_manifest(package, revision="a" * 40)
    assert run_cli(package, "init", "--data-dir", data).returncode == 0
    paths = {"editing": data / "editing-state/editing-state.sqlite3", "queue": data / "graph-sync.sqlite3"}
    for kind, path in paths.items():
        path.parent.mkdir(exist_ok=True)
        db = sqlite3.connect(path)
        try:
            initialize_store(db, kind)
        finally:
            db.close()
    before = {kind: path.read_bytes() for kind, path in paths.items()}
    (package / "src" / "revision.py").write_text("revision=2\n")
    second = write_release_manifest(package, revision="b" * 40)
    contract = RuntimeContract(data, first["source_sha256"])
    result = run_cli(package, "init", "--data-dir", data)
    assert result.returncode == 2
    assert json.loads(result.stderr)["code"] == "state_contract_invalid"
    for expected in ("invalid", "a" * 63, "g" * 64):
        result = run_cli(package, "activate", "--data-dir", data, "--expected-source", expected, "--writers-stopped")
        assert result.returncode == 2
        assert json.loads(result.stderr)["code"] == "preparation_failed"
        assert contract.inspect()["epoch"] == 1
    db = sqlite3.connect(paths["queue"])
    try:
        # IMMEDIATE allows readers; EXCLUSIVE prevents the preflight snapshot.
        db.execute("BEGIN EXCLUSIVE")
        result = run_cli(package, "activate", "--data-dir", data,
                         "--expected-source", first["source_sha256"], "--writers-stopped")
        assert result.returncode == 2
        assert json.loads(result.stderr)["code"] == "state_contract_busy"
        assert contract.inspect()["epoch"] == 1
    finally:
        db.rollback()
        db.close()
    result = run_cli(package, "activate", "--data-dir", data,
                     "--expected-source", first["source_sha256"], "--writers-stopped")
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert json.loads(result.stdout)["runtime"]["source_sha256"] == second["source_sha256"]
    assert contract.inspect()["epoch"] == 2
    assert {kind: path.read_bytes() for kind, path in paths.items()} == before


@pytest.mark.parametrize("sqlite_code, expected", [(sqlite3.SQLITE_FULL, "state_contract_invalid"),
                                                   (sqlite3.SQLITE_BUSY, "state_contract_busy")])
def test_init_sqlite_failure_is_json_and_leaves_no_partial_database(package, tmp_path, monkeypatch, capsys,
                                                                  sqlite_code, expected):
    from tools import prepare_runtime as cli
    write_release_manifest(package, revision="a" * 40)
    monkeypatch.setattr(cli, "ROOT", package)
    data = tmp_path / "data"

    def fail(*args, **kwargs):
        error = sqlite3.OperationalError("synthetic sqlite failure")
        error.sqlite_errorcode = sqlite_code
        raise error
    monkeypatch.setattr(sqlite3, "connect", fail)
    assert cli.main(["init", "--data-dir", str(data)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["code"] == expected
    assert not (data / "runtime-contract.sqlite3").exists()
    assert not list(data.glob(".runtime-contract-*.tmp"))


def test_preflight_io_failure_preserves_contract_and_releases_exclusion(package, tmp_path, monkeypatch, capsys):
    from tools import prepare_runtime as cli
    monkeypatch.setattr(cli, "ROOT", package)
    first = write_release_manifest(package, revision="a" * 40)
    data = tmp_path / "data"
    assert cli.main(["init", "--data-dir", str(data)]) == 0
    capsys.readouterr()
    (package / "src" / "revision.py").write_text("revision=2\n")
    write_release_manifest(package, revision="b" * 40)
    command = ["activate", "--data-dir", str(data), "--expected-source", first["source_sha256"], "--writers-stopped"]
    contract = RuntimeContract(data, first["source_sha256"])
    snapshot = contract.inspect()

    def fail(*args):
        raise PermissionError("synthetic unavailable storage")
    with monkeypatch.context() as context:
        context.setattr(cli, "preflight_stores", fail)
        assert cli.main(command) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "preparation_failed"
    assert contract.inspect() == snapshot
    assert cli.main(command) == 0
    assert json.loads(capsys.readouterr().out)["runtime"]["epoch"] == 2
