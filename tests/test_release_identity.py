"""Release identity observed by web, worker and the packaging command."""
import json
from pathlib import Path
import subprocess
import sys
import pytest
from src.services.release_identity import get_release_info


def test_source_change_changes_release_identity_but_line_endings_do_not(tmp_path):
    source = tmp_path / "src" / "entry.py"
    source.parent.mkdir()
    source.write_bytes(b"value = 1\n")
    first = get_release_info(tmp_path)
    source.write_bytes(b"value = 1\r\n")
    assert get_release_info(tmp_path)["source_sha256"] == first["source_sha256"]
    source.write_bytes(b"value = 2\n")
    assert get_release_info(tmp_path)["source_sha256"] != first["source_sha256"]
    assert first["revision"] == "working-tree"
    assert first["verified"] is False


def test_packaged_release_refuses_modified_or_missing_source(tmp_path):
    from src.services.release_identity import write_release_manifest, ReleaseIdentityError
    source = tmp_path / "src" / "entry.py"
    source.parent.mkdir()
    source.write_text("value = 1\n", encoding="utf-8")
    write_release_manifest(tmp_path, revision="a" * 40)
    assert get_release_info(tmp_path)["revision"] == "a" * 40
    assert get_release_info(tmp_path)["verified"] is True
    source.write_text("value = 2\n", encoding="utf-8")
    with pytest.raises(ReleaseIdentityError, match="corresponde"):
        get_release_info(tmp_path)
    source.unlink()
    with pytest.raises(ReleaseIdentityError, match="corresponde"):
        get_release_info(tmp_path)


def test_web_processes_and_worker_report_the_same_source_identity():
    from src.web.application import create_app
    from src.queue_worker import build_queue
    expected = get_release_info()
    first = create_app().test_client().get("/static/css/field-app.css")
    second = create_app().test_client().get("/static/css/field-app.css")
    assert first.status_code == second.status_code == 200
    assert first.headers["X-App-Version"] == second.headers["X-App-Version"] == expected["source_sha256"]
    assert build_queue().release_info == expected


def test_release_builder_records_the_requested_commit_and_refuses_invalid_revision(tmp_path):
    source = tmp_path / "src" / "entry.py"
    source.parent.mkdir()
    source.write_text("value = 1\n", encoding="utf-8")
    command = [sys.executable, "-B", str(Path(__file__).resolve().parents[1] / "tools/build_release.py"),
               "--root", str(tmp_path), "--revision", "b" * 40]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["revision"] == "b" * 40
    previous = (tmp_path / "release-manifest.json").read_bytes()
    command[-1] = "not-a-commit"
    assert subprocess.run(command, capture_output=True, timeout=30).returncode != 0
    assert (tmp_path / "release-manifest.json").read_bytes() == previous
