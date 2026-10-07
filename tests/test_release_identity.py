"""Release identity observed by web, worker and the packaging command."""
import json
from pathlib import Path
import subprocess
import sys
import shutil
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


def test_web_and_worker_refuse_a_mismatched_package_and_worker_logs_the_failure(tmp_path, caplog):
    from src.services.release_identity import write_release_manifest, ReleaseIdentityError
    from src.web.application import create_app
    from src.queue_worker import build_queue, main
    source = tmp_path / "src" / "entry.py"
    source.parent.mkdir()
    source.write_text("value = 1\n", encoding="utf-8")
    write_release_manifest(tmp_path, revision="c" * 40)
    source.write_text("value = 2\n", encoding="utf-8")
    for factory in (create_app, build_queue):
        with pytest.raises(ReleaseIdentityError):
            factory(release_root=tmp_path)
    assert main([], release_root=tmp_path) == 1
    assert any(getattr(record, "event", None) == "queue_worker_failed" for record in caplog.records)


def test_rendered_asset_urls_use_the_content_identity(tmp_path):
    from src.web.application import create_app
    source = tmp_path / "src" / "entry.py"
    source.parent.mkdir()
    source.write_text("value = 1\n", encoding="utf-8")
    expected = get_release_info(tmp_path)["source_sha256"][:16]
    for _ in range(2):
        response = create_app(release_root=tmp_path).test_client().get("/")
        assert response.status_code == 200
        assert ("v=" + expected).encode() in response.data


def test_manifest_failure_identifies_paths_without_disclosing_file_contents(tmp_path):
    from src.services.release_identity import write_release_manifest, ReleaseIdentityError
    source = tmp_path / "src" / "entry.py"
    source.parent.mkdir()
    source.write_text("value = 1\n", encoding="utf-8")
    removed = source.with_name("removed.py")
    removed.write_text("value = 2\n", encoding="utf-8")
    write_release_manifest(tmp_path, revision="d" * 40)
    source.write_text("private fictitious content\n", encoding="utf-8")
    removed.unlink()
    source.with_name("added.py").write_text("new content\n", encoding="utf-8")
    with pytest.raises(ReleaseIdentityError) as failure:
        get_release_info(tmp_path)
    message = str(failure.value)
    assert all(name in message for name in ("src/entry.py", "src/removed.py", "src/added.py"))
    assert "private fictitious content" not in message


def test_webmanifest_has_the_same_identity_on_windows_and_linux(tmp_path):
    source = tmp_path / "src" / "manifest.webmanifest"
    source.parent.mkdir()
    source.write_bytes(b'{\n  "name": "Synthetic"\n}\n')
    expected = get_release_info(tmp_path)["source_sha256"]
    source.write_bytes(b'\xef\xbb\xbf{\r\n  "name": "Synthetic"\r\n}\r\n')
    assert get_release_info(tmp_path)["source_sha256"] == expected


def test_broken_source_symlink_is_refused_instead_of_ignored(tmp_path):
    from src.services.release_identity import ReleaseIdentityError
    source = tmp_path / "src"
    source.mkdir()
    try:
        (source / "broken.py").symlink_to(source / "missing.py")
    except OSError:
        pytest.skip("O sistema não permite criar symlinks neste ensaio.")
    with pytest.raises(ReleaseIdentityError, match="simbólicas"):
        get_release_info(tmp_path)


def test_python_cache_does_not_change_identity_but_extra_source_files_do(tmp_path):
    source = tmp_path / "src" / "entry.py"
    source.parent.mkdir()
    source.write_text("value = 1\n", encoding="utf-8")
    expected = get_release_info(tmp_path)["source_sha256"]
    cache = source.parent / "__pycache__"
    cache.mkdir()
    (cache / "entry.pyc").write_bytes(b"synthetic bytecode")
    assert get_release_info(tmp_path)["source_sha256"] == expected
    source.with_suffix(".orig").write_text("old source", encoding="utf-8")
    assert get_release_info(tmp_path)["source_sha256"] != expected


def test_packaging_does_not_import_runtime_configuration(tmp_path):
    source = tmp_path / "src" / "entry.py"
    source.parent.mkdir()
    source.write_text("value = 1\n", encoding="utf-8")
    tool = Path(__file__).resolve().parents[1] / "tools/build_release.py"
    probe = (
        "import runpy,sys; "
        "tool=sys.argv.pop(1); "
        "module=runpy.run_path(tool,run_name='packaging_probe'); "
        "assert module['main']()==0; "
        "assert 'src.config' not in sys.modules; "
        "assert 'src.web.application' not in sys.modules"
    )
    result = subprocess.run([sys.executable, "-B", "-c", probe, str(tool),
                             "--root", str(tmp_path), "--revision", "e" * 40],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("git_marker", ["omitted-root", "directory", "worktree-file"])
def test_release_builder_requires_explicit_staging_outside_a_git_checkout(tmp_path, git_marker):
    # Copy only the standalone command/module, so even the old default cannot
    # write a manifest in the real development checkout during the red test.
    repository = Path(__file__).resolve().parents[1]
    tool = tmp_path / "tools" / "build_release.py"
    service = tmp_path / "src" / "services" / "release_identity.py"
    tool.parent.mkdir()
    service.parent.mkdir(parents=True)
    shutil.copyfile(repository / "tools" / tool.name, tool)
    shutil.copyfile(repository / "src" / "services" / service.name, service)
    command = [sys.executable, "-B", str(tool), "--revision", "f" * 40]
    if git_marker != "omitted-root":
        command += ["--root", str(tmp_path)]
        marker = tmp_path / ".git"
        if git_marker == "directory":
            marker.mkdir()
        else:
            marker.write_text("gitdir: synthetic-worktree\n", encoding="utf-8")
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 2, result.stdout
    assert ("--root" if git_marker == "omitted-root" else "Git") in result.stderr
    assert not (tmp_path / "release-manifest.json").exists()


def test_unsupported_python_is_refused_with_an_actionable_message(tmp_path, monkeypatch):
    from src.services.release_identity import ReleaseIdentityError, write_release_manifest
    (tmp_path / "src").mkdir()
    monkeypatch.setattr(sys, "version_info", (3, 11, 9))
    with pytest.raises(ReleaseIdentityError, match="Python 3.12"):
        get_release_info(tmp_path)
    with pytest.raises(ReleaseIdentityError, match="Python 3.12"):
        write_release_manifest(tmp_path, revision="a" * 40)
    assert not (tmp_path / "release-manifest.json").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Junções de diretório são específicas de Windows.")
def test_windows_source_junction_is_refused(tmp_path):
    from src.services.release_identity import ReleaseIdentityError
    source = tmp_path / "src"
    source.mkdir()
    target = tmp_path / "synthetic-target"
    target.mkdir()
    (target / "entry.py").write_text("value = 1\n", encoding="utf-8")
    script = tmp_path / "junction.ps1"
    script.write_text(
        'param([string]$JunctionPath, [string]$TargetPath)\n'
        '$ErrorActionPreference = "Stop"\n'
        'New-Item -ItemType Junction -Path $JunctionPath -Target $TargetPath | Out-Null\n',
        encoding="utf-8",
    )
    junction = source / "linked"
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(script), "-JunctionPath", str(junction), "-TargetPath", str(target)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    try:
        with pytest.raises(ReleaseIdentityError, match="junções"):
            get_release_info(tmp_path)
    finally:
        # Remove only this synthetic junction, never recurse into its target.
        junction.rmdir()
    assert (target / "entry.py").read_text(encoding="utf-8") == "value = 1\n"


def test_manifest_diagnostic_bounds_and_escapes_many_long_paths(tmp_path):
    from src.services.release_identity import ReleaseIdentityError, write_release_manifest
    # Windows otherwise reaches MAX_PATH before exercising diagnostic truncation.
    root = Path("\\\\?\\" + str(tmp_path)) if sys.platform == "win32" else tmp_path
    source = root / "src"
    source.mkdir()
    write_release_manifest(root, revision="a" * 40)
    for index in range(20):
        (source / (f"{index:02d}_ç" + "x" * 165 + ".py")).write_text(
            "synthetic private content\n", encoding="utf-8"
        )
    with pytest.raises(ReleaseIdentityError) as failure:
        get_release_info(root)
    detail_text = str(failure.value).split(". ", 1)[1]
    assert detail_text.isascii()
    detail = json.loads(detail_text)
    assert len(detail["adicionados"]) == 12
    assert all(len(name) <= 160 for name in detail["adicionados"])
    assert "synthetic private content" not in str(failure.value)
