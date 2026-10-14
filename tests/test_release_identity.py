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


def test_manifest_has_exact_lf_bytes_for_windows_and_linux_source_trees(tmp_path):
    from src.services.release_identity import write_release_manifest
    manifests = []
    for name, newline in (("linux", b"\n"), ("windows", b"\r\n")):
        staging = tmp_path / name
        (staging / "src").mkdir(parents=True)
        (staging / "src/entry.py").write_bytes(b"synthetic = 1" + newline)
        write_release_manifest(staging, revision="a" * 40)
        raw = (staging / "release-manifest.json").read_bytes()
        assert raw == (json.dumps(json.loads(raw), sort_keys=True) + "\n").encode("utf-8")
        assert b"\r" not in raw
        manifests.append(raw)
    assert manifests[0] == manifests[1]


def test_web_processes_and_worker_report_the_same_source_identity():
    from src.web.application import create_app
    from src.queue_worker import build_queue
    expected = get_release_info()
    first = create_app().test_client().get("/static/css/field-app.css")
    second = create_app().test_client().get("/static/css/field-app.css")
    assert first.status_code == second.status_code == 200
    assert first.headers["X-App-Version"] == second.headers["X-App-Version"] == expected["source_sha256"]
    assert build_queue().release_info == expected


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
    from src.services.runtime_contract import RuntimeContract
    identity = get_release_info(tmp_path)["source_sha256"]
    contract = RuntimeContract(tmp_path / "data", identity)
    contract.initialize_empty()
    expected = identity[:16]
    for _ in range(2):
        response = create_app(release_root=tmp_path, runtime_contract=contract).test_client().get("/")
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


@pytest.mark.parametrize("git_marker", ["directory", "worktree-file"])
def test_release_builder_refuses_staging_inside_a_git_ancestor(tmp_path, git_marker):
    from src.services.release_identity import ReleaseIdentityError, write_release_manifest
    repository = tmp_path / "repository"
    staging = repository / "sub" / "staging"
    source = staging / "src"
    source.mkdir(parents=True)
    (source / "entry.py").write_text("value = 1\n", encoding="utf-8")
    marker = repository / ".git"
    if git_marker == "directory":
        marker.mkdir()
    else:
        marker.write_text("gitdir: synthetic-worktree\n", encoding="utf-8")
    with pytest.raises(ReleaseIdentityError, match="Git"):
        write_release_manifest(staging, revision="a" * 40)
    assert not (staging / "release-manifest.json").exists()
    assert not list(staging.glob(".release-*.tmp"))


@pytest.mark.parametrize("environment", ["production", "development", "test"])
def test_operational_factories_require_manifest_before_creating_data(tmp_path, monkeypatch, environment):
    from src.services.release_identity import ReleaseIdentityError
    from src.services.runtime_contract import RuntimeContract
    from src.web.application import create_app
    from src.queue_worker import build_queue
    (tmp_path / "src").mkdir()
    (tmp_path / "src/entry.py").write_text("synthetic = 1\n", encoding="utf-8")
    monkeypatch.setenv("FS_ENVIRONMENT", environment)
    monkeypatch.setenv("FS_TEST_SYNTHETIC", "true" if environment == "production" else "false")
    data = tmp_path / "absent-data"
    contract = RuntimeContract(data, get_release_info(tmp_path)["source_sha256"])
    for factory in (create_app, build_queue):
        with pytest.raises(ReleaseIdentityError, match="manifesto"):
            factory(release_root=tmp_path, runtime_contract=contract)
    assert not data.exists()


@pytest.mark.parametrize("name", ["tools/prepare_runtime.py", "tools/backup_runtime.py", "Dockerfile",
                                  "docker-compose.yml", ".dockerignore", "requirements.txt"])
def test_packaged_operational_tools_and_delivery_files_are_verified(tmp_path, name):
    from src.services.release_identity import write_release_manifest, ReleaseIdentityError
    (tmp_path / "src").mkdir()
    (tmp_path / "src/entry.py").write_text("synthetic = 1\n", encoding="utf-8")
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("synthetic original\n", encoding="utf-8")
    write_release_manifest(tmp_path, revision="a" * 40)
    path.write_text("synthetic changed\n", encoding="utf-8")
    with pytest.raises(ReleaseIdentityError, match="corresponde"):
        get_release_info(tmp_path)


def test_persistent_data_inside_tools_is_refused_before_fingerprinting(tmp_path):
    from src.services.runtime_admission import resolve_runtime
    from src.services.runtime_contract import ContractError
    (tmp_path / "src").mkdir()
    with pytest.raises(ContractError, match="dados.*código"):
        resolve_runtime(tmp_path / "tools/private", release_root=tmp_path)


def test_verified_package_is_admitted_even_in_production(monkeypatch, verified_test_release):
    from src.web.application import create_app
    from src.queue_worker import build_queue
    monkeypatch.setenv("FS_ENVIRONMENT", "production")
    monkeypatch.delenv("FS_TEST_SYNTHETIC", raising=False)
    app = create_app(release_root=verified_test_release)
    queue = build_queue(release_root=verified_test_release)
    expected = get_release_info(verified_test_release)
    assert expected["verified"]
    assert app.test_client().get("/static/css/field-app.css").headers["X-App-Version"] == expected["source_sha256"]
    assert queue.release_info == expected


@pytest.mark.parametrize("unsafe", ["FS_STORAGE_BACKEND", "FS_AUTH_PROVIDER", "FS_MAIL_ENABLED",
                                  "FS_TEAMS_NOTIFICATIONS_ENABLED", "FS_GRAPH_QUEUE_IN_WEB"])
def test_incomplete_synthetic_profile_does_not_bypass_manifest(monkeypatch, tmp_path, unsafe):
    from src.services.runtime_admission import resolve_runtime
    from src.services.release_identity import ReleaseIdentityError
    monkeypatch.setenv(unsafe, "graph" if unsafe == "FS_STORAGE_BACKEND" else "true")
    # This condition must be explicit: the suite also runs in a verified ZIP.
    missing_manifest = tmp_path / "unpackaged"
    (missing_manifest / "src").mkdir(parents=True)
    with pytest.raises(ReleaseIdentityError, match="manifesto"):
        resolve_runtime(tmp_path / "data", release_root=missing_manifest)
