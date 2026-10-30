"""Packaging an exact clean commit, using only fictional Git repositories."""
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from src.services.release_identity import get_release_info

ROOT = Path(__file__).resolve().parents[1]


def git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)
    return result.stdout.decode("utf-8").strip()


@pytest.fixture
def source_repo(tmp_path):
    repo = tmp_path / "synthetic-repo"
    repo.mkdir()
    git(repo, "init", "-q")
    files = {
        "src/web/application.py": "synthetic = 1\n",
        "src/queue_worker.py": "synthetic = 1\n",
        "src/services/release_identity.py": "synthetic = 1\n",
        "tools/build_release.py": "synthetic = 1\n",
        "tools/prepare_runtime.py": "synthetic = 1\n",
        "tools/migrate_runtime.py": "synthetic = 1\n",
        "tools/backup_runtime.py": "synthetic = 1\n",
        "passenger_wsgi.py": "synthetic = 1\n",
        "requirements-server.txt": "synthetic==1\n",
        "requirements-smoke.txt": "synthetic==1\n",
        "requirements-linux-constraints.txt": "synthetic==1\n",
        "Dockerfile": "FROM synthetic\n",
        "docker-compose.yml": "services: {}\n",
        ".dockerignore": ".env\n",
        ".gitignore": ".env\nprivate-data/\n",
        ".gitattributes": "src/queue_worker.py export-ignore\n",
        "docs/README.md": "Synthetic documentation\n",
        "tests/test_synthetic.py": "def test_synthetic(): assert True\n",
    }
    for name, data in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid", "commit", "-qm", "Synthetic release")
    return repo, git(repo, "rev-parse", "HEAD")


def build(repo, revision, destination):
    return subprocess.run([sys.executable, "-B", str(ROOT / "tools/build_release.py"),
                           "--repo", str(repo), "--revision", revision, "--output", str(destination)],
                          capture_output=True, text=True, timeout=60)


def test_builds_identical_artifacts_from_commit_without_private_or_git_data(source_repo, tmp_path):
    repo, revision = source_repo
    (repo / ".env").write_text("SYNTHETIC_PRIVATE=must-stay-out\n", encoding="utf-8")
    (repo / "private-data").mkdir()
    (repo / "private-data/queue.sqlite3").write_bytes(b"fictional private data")
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    for target in (first, second):
        result = build(repo, revision, target)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["revision"] == revision
    assert first.read_bytes() == second.read_bytes()
    staging = tmp_path / "unpacked"
    with zipfile.ZipFile(first) as archive:
        assert "src/queue_worker.py" in archive.namelist()  # export-ignore cannot silently omit code.
        assert "tools/backup_runtime.py" in archive.namelist()
        assert "tests/test_synthetic.py" in archive.namelist()
        manifest = archive.read("release-manifest.json")
        assert manifest == (json.dumps(json.loads(manifest), sort_keys=True) + "\n").encode("utf-8")
        assert not any(name.startswith((".git", ".env", "private-data/")) for name in archive.namelist())
        archive.extractall(staging)
    info = get_release_info(staging)
    assert info["verified"] and info["revision"] == revision
    assert not (repo / "release-manifest.json").exists()


@pytest.mark.parametrize("change", ["tracked", "untracked", "staged", "wrong-sha", "short-sha", "existing", "missing-file", "missing-constraints"])
def test_refuses_unreviewed_or_incomplete_release_without_overwriting(source_repo, tmp_path, change):
    repo, revision = source_repo
    destination = tmp_path / "result.zip"
    if change in {"tracked", "staged"}:
        (repo / "src/queue_worker.py").write_text("changed = 1\n", encoding="utf-8")
        if change == "staged":
            git(repo, "add", "src/queue_worker.py")
    elif change == "untracked":
        (repo / "src/new.py").write_text("new = 1\n", encoding="utf-8")
    elif change == "wrong-sha":
        revision = "f" * 40
    elif change == "short-sha":
        revision = revision[:8]
    elif change == "existing":
        destination.write_bytes(b"earlier synthetic release")
    elif change in {"missing-file", "missing-constraints"}:
        git(repo, "rm", "tools/backup_runtime.py" if change == "missing-file" else "requirements-linux-constraints.txt")
        git(repo, "-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid", "commit", "-qm", "Missing tool")
        revision = git(repo, "rev-parse", "HEAD")
    result = build(repo, revision, destination)
    assert result.returncode == 2, result.stdout
    assert not destination.exists() or destination.read_bytes() == b"earlier synthetic release"


def test_builder_does_not_import_application_or_private_configuration(source_repo, tmp_path):
    repo, revision = source_repo
    probe = ("import runpy,sys; tool=sys.argv.pop(1); module=runpy.run_path(tool,run_name='probe'); "
             "assert module['main']()==0; assert 'src.config' not in sys.modules; "
             "assert 'src.web.application' not in sys.modules")
    result = subprocess.run([sys.executable, "-B", "-c", probe, str(ROOT / "tools/build_release.py"),
                             "--repo", str(repo), "--revision", revision, "--output", str(tmp_path / "probe.zip")],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name,accepted", [("tests/fixtures/test_sample.xlsx", True),
                                          ("tests/fixtures/customer.xlsx", False)])
def test_only_the_established_synthetic_workbook_is_packaged(source_repo, tmp_path, name, accepted):
    repo, revision = source_repo
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"synthetic workbook placeholder")
    git(repo, "add", name)
    git(repo, "-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid", "commit", "-qm", "Synthetic workbook")
    revision = git(repo, "rev-parse", "HEAD")
    result = build(repo, revision, tmp_path / "fixture.zip")
    assert result.returncode == (0 if accepted else 2), result.stderr
    if accepted:
        with zipfile.ZipFile(tmp_path / "fixture.zip") as archive:
            assert archive.read(name) == b"synthetic workbook placeholder"
    else:
        assert not (tmp_path / "fixture.zip").exists()
