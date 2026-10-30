"""Read-only readiness diagnostics over synthetic copies of historic shapes."""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from tests.legacy_editing_shapes import version_one, version_two, legacy_lease, snapshot_name
import pytest


def test_inspect_copy_classifies_legacy_and_current_without_mutation(tmp_path, capsys):
    from tools.inspect_editing_states import main
    database = tmp_path / "copy.sqlite3"
    forms = {"v1": version_one("v1"), "v2": version_two("v2"), "lease": legacy_lease("lease"),
             "partial": {**version_one("partial"), "version": 2},
             "future": {**version_two("future"), "version": 99}}
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE editing_states(document_id TEXT PRIMARY KEY,state_json TEXT,updated_at REAL)")
        db.executemany("INSERT INTO editing_states VALUES (?,?,1)",
                       [(key, json.dumps(value)) for key, value in forms.items()] + [("broken", '{"secret-synthetic":')])
    before = database.read_bytes()
    assert main(["--database-copy", str(database)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["total"] == 6
    assert result["counts"] == {"compatible": 1, "legacy_version": 2, "incomplete_current": 1,
                                "unsupported_version": 1, "invalid_state": 1}
    assert result["ready"] is False
    assert "secret-synthetic" not in json.dumps(result)
    assert all("document_ref" in row and "state_json" not in row for row in result["records"])
    assert database.read_bytes() == before


def test_inspect_legacy_json_copy_does_not_import_or_rewrite(tmp_path, capsys):
    from tools.inspect_editing_states import main
    snapshot = tmp_path / snapshot_name("legacy")
    snapshot.write_text(json.dumps(legacy_lease("legacy")), encoding="utf-8")
    before = snapshot.read_bytes()
    assert main(["--json-directory-copy", str(tmp_path)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["counts"]["legacy_version"] == 1
    assert snapshot.read_bytes() == before
    assert list(tmp_path.iterdir()) == [snapshot]


@pytest.mark.parametrize("name", ["current.json", snapshot_name("different-id")])
def test_json_filename_must_match_embedded_identity(tmp_path, capsys, name):
    from tools.inspect_editing_states import main
    snapshot = tmp_path / name
    snapshot.write_text(json.dumps(version_two("current")), encoding="utf-8")
    before = snapshot.read_bytes()
    assert main(["--json-directory-copy", str(tmp_path)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["counts"] == {"invalid_state": 1} and result["ready"] is False
    assert snapshot.read_bytes() == before


def test_json_copy_classifies_current_and_damaged_records_independently(tmp_path, capsys):
    from tools.inspect_editing_states import main
    snapshots = {snapshot_name("current"): json.dumps(version_two("current")),
                 snapshot_name("old"): json.dumps(legacy_lease("old")),
                 snapshot_name("damaged"): '{"document_id":',
                 snapshot_name("missing"): '{"version":2}',
                 snapshot_name("duplicate"): '{"document_id":"duplicate","document_id":"duplicate"}'}
    for name, raw in snapshots.items():
        (tmp_path / name).write_text(raw, encoding="utf-8")
    assert main(["--json-directory-copy", str(tmp_path)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["counts"] == {"compatible": 1, "legacy_version": 1, "invalid_state": 3}
    assert {path.name: path.read_text(encoding="utf-8") for path in tmp_path.iterdir()} == snapshots


def test_missing_copy_is_not_created(tmp_path, capsys):
    from tools.inspect_editing_states import main
    absent = tmp_path / "absent.sqlite3"
    assert main(["--database-copy", str(absent)]) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "inspection_failed"
    assert not absent.exists()


def test_cli_valid_copy_without_loading_application_or_configuration(tmp_path):
    database = tmp_path / "copy.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE editing_states(document_id TEXT,state_json TEXT)")
        db.execute("INSERT INTO editing_states VALUES (?,?)", ("synthetic", json.dumps(version_two("synthetic"))))
    script = Path(__file__).resolve().parents[1] / "tools/inspect_editing_states.py"
    guard = '''import importlib.abc,runpy,sys
class Deny(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "src.config" or fullname.startswith(("src.web", "src.services.graph", "src.services.microsoft")):
            raise RuntimeError("Operational import forbidden")
sys.meta_path.insert(0,Deny())
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0],run_name="__main__")
'''
    result = subprocess.run([sys.executable, "-B", "-c", guard, str(script), "--database-copy", str(database)],
                            cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["counts"] == {"compatible": 1}
