"""Build a fixed source artifact from Git objects, without importing the app."""
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import zipfile

from src.services.release_identity import (
    ReleaseIdentityError, MANIFEST_NAME, is_delivery_file, write_release_manifest,
)

REQUIRED = {
    "src/web/application.py", "src/queue_worker.py", "src/services/release_identity.py",
    "tools/build_release.py", "tools/prepare_runtime.py", "tools/migrate_runtime.py",
    "tools/backup_runtime.py", "passenger_wsgi.py", "requirements-server.txt",
    "requirements-smoke.txt", "requirements-linux-constraints.txt",
    "Dockerfile", "docker-compose.yml", ".dockerignore",
}


def _git(repo, *args, input=None):
    result = subprocess.run(["git", "-C", str(repo), *args], input=input,
                            capture_output=True, timeout=120)
    if result.returncode:
        # Do not echo Git diagnostics: a remote URL/configuration can be private.
        raise ReleaseIdentityError("Não foi possível ler o repositório Git local.")
    return result.stdout


def _clean_head(repo, revision):
    if _git(repo, "rev-parse", "HEAD").decode().strip() != revision:
        raise ReleaseIdentityError("O SHA indicado tem de ser o HEAD completo do checkout testado.")
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ReleaseIdentityError("O checkout tem alterações; concluir e testar o commit antes de empacotar.")


def _included(name):
    path = PurePosixPath(name)
    if (path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name
            or name != path.as_posix()):
        raise ReleaseIdentityError("Caminho Git inválido para entrega.")
    if len(path.parts) == 1:
        return is_delivery_file(name) or name in {"README.md", "PATCH_NOTES.md", ".env.example", ".env.production.example"}
    if path.parts[0] not in {"src", "tools", "docs", "tests"}:
        return False
    # This exact, repository-reviewed workbook was generated with fictional
    # data and is required by the integration suite. No general Excel allowlist.
    if name == "tests/fixtures/test_sample.xlsx":
        return True
    # Operational data is never part of a release, even if accidentally tracked.
    if (any(part in {".git", "__pycache__", "data", "private-data", "Excel", "PDF"} or part.startswith(".env") for part in path.parts)
            or path.suffix.lower() in {".pyc", ".pyo", ".sqlite", ".sqlite3", ".db", ".log", ".jsonl", ".xlsx", ".xlsm", ".pdf"}
            or any(".sqlite" in part for part in path.parts)):
        raise ReleaseIdentityError("O código versionado inclui dados/cache excluídos da entrega; rever o commit.")
    return True


def build_release(repo, revision, destination):
    """Produce the same ZIP bytes for the same commit; never overwrite a package.

    Read blobs directly instead of copying working files or using git archive:
    filters, line-ending settings and export-ignore must not change the artifact.
    The manifest is integrity metadata, not a signature of a trusted supplier.
    """
    if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ReleaseIdentityError("Indique o SHA Git completo da versão testada.")
    repo, destination = Path(repo).resolve(), Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ReleaseIdentityError("O destino já existe; não substituir uma entrega anterior.")
    if not destination.parent.is_dir():
        raise ReleaseIdentityError("A pasta de destino tem de existir.")
    _clean_head(repo, revision)
    entries = []
    for entry in _git(repo, "ls-tree", "-rz", "--full-tree", revision).split(b"\0"):
        if not entry:
            continue
        metadata, raw_name = entry.split(b"\t", 1)
        mode, kind, oid = metadata.split()
        name = raw_name.decode("utf-8")
        if not _included(name):
            continue
        if kind != b"blob" or mode not in {b"100644", b"100755"}:
            raise ReleaseIdentityError("A entrega não admite symlinks ou submódulos nos caminhos selecionados.")
        entries.append((name, oid, int(mode, 8)))
    if not REQUIRED.issubset({name for name, _, _ in entries}):
        raise ReleaseIdentityError("Faltam ficheiros obrigatórios da aplicação/ferramentas/entrega no commit.")
    entries.sort()
    blobs = io.BytesIO(_git(repo, "cat-file", "--batch", input=b"".join(oid + b"\n" for _, oid, _ in entries)))
    # TemporaryDirectory is outside the checkout; low-level manifest writing
    # deliberately refuses any Git ancestor. No .env is parsed or imported.
    with tempfile.TemporaryDirectory(prefix="fs-release-") as temporary:
        staging = Path(temporary)
        modes = {}
        for name, oid, mode in entries:
            header = blobs.readline().strip().split()
            if len(header) != 3 or header[:2] != [oid, b"blob"]:
                raise ReleaseIdentityError("Resposta Git incompleta durante o empacotamento.")
            length = int(header[2])
            content = blobs.read(length)
            if len(content) != length or blobs.read(1) != b"\n":
                raise ReleaseIdentityError("Blob Git incompleto durante o empacotamento.")
            path = staging / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            modes[name] = mode
        info = write_release_manifest(staging, revision=revision)
        modes[MANIFEST_NAME] = 0o100644
        _clean_head(repo, revision)  # Refuse concurrent edits before publication.
        # Exclusive creation prevents a concurrent builder overwriting a release.
        # On failure, preserve a partial ZIP for diagnosis and choose a new name.
        with destination.open("xb") as stream:
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
                for name, mode in sorted(modes.items()):
                    member = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    member.create_system = 3
                    member.external_attr = mode << 16
                    archive.writestr(member, (staging / name).read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
    return {**info, "artifact": str(destination), "artifact_sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}
