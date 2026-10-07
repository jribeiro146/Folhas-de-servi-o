"""Identity of the code and declared dependencies, without importing the app."""
from __future__ import annotations

import hashlib
import json
import re
import sys
import uuid
from pathlib import Path, PurePosixPath

MANIFEST_NAME = "release-manifest.json"


class ReleaseIdentityError(RuntimeError):
    """The running files do not match the packaged release."""


def _source_fingerprint(root: Path) -> tuple[str, dict[str, str]]:
    if sys.version_info < (3, 12):
        raise ReleaseIdentityError("Esta versão requer Python 3.12 ou superior; usar preferencialmente Python 3.13.")
    source_root = root / "src"
    if source_root.is_symlink() or source_root.is_junction():
        raise ReleaseIdentityError("O pacote não pode conter ligações simbólicas ou junções no código.")
    if not (root / "src").is_dir():
        raise ReleaseIdentityError("Pasta de código da aplicação em falta.")
    paths = []
    for path in source_root.rglob("*"):
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        if path.is_symlink() or path.is_junction():
            raise ReleaseIdentityError("O pacote não pode conter ligações simbólicas ou junções no código.")
        if path.is_file():
            paths.append(path)
    paths += [root / name for name in ("passenger_wsgi.py", "requirements-server.txt", "requirements-smoke.txt")
              if (root / name).is_file()]
    digest = hashlib.sha256()
    files = {}
    for path in sorted(paths, key=lambda p: p.relative_to(root).as_posix()):
        if path.is_symlink():
            raise ReleaseIdentityError("O pacote não pode conter ligações simbólicas no código.")
        data = path.read_bytes()
        if path.suffix in {".py", ".js", ".css", ".html", ".json", ".svg", ".txt", ".webmanifest"}:
            data = data.removeprefix(b"\xef\xbb\xbf").replace(b"\r\n", b"\n")
        name = path.relative_to(root).as_posix()
        file_digest = hashlib.sha256(data).digest()
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(file_digest)
        files[name] = file_digest.hex()
    return digest.hexdigest(), files


def get_release_info(root: Path | None = None) -> dict:
    root = Path(root) if root is not None else Path(__file__).absolute().parents[2]
    fingerprint, files = _source_fingerprint(root)
    count = len(files)
    info = {"revision": "working-tree", "source_sha256": fingerprint,
            "source_files": count, "verified": False}
    manifest = root / MANIFEST_NAME
    if not manifest.exists():
        return info
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or type(data.get("format")) is not int or data["format"] != 1
                or not isinstance(data.get("revision"), str)
                or re.fullmatch(r"[0-9a-f]{40}", data["revision"]) is None
                or type(data.get("source_files")) is not int
                or not isinstance(data.get("files"), dict)):
            raise ValueError("Invalid release manifest")
        expected = data["files"]
        for name, value in expected.items():
            if (not isinstance(name, str) or PurePosixPath(name).is_absolute()
                    or ".." in PurePosixPath(name).parts or "\\" in name
                    or not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None):
                raise ValueError("Invalid release file entry")
        if data.get("source_sha256") != fingerprint or data["source_files"] != count or expected != files:
            differences = {
                "adicionados": sorted(files.keys() - expected.keys()),
                "removidos": sorted(expected.keys() - files.keys()),
                "alterados": sorted(name for name in files.keys() & expected.keys() if files[name] != expected[name]),
            }
            # Relative names only, escaped and bounded; never include source contents.
            detail = {key: [name[:160] for name in names[:12]] for key, names in differences.items() if names}
            raise ReleaseIdentityError(
                "O código não corresponde ao manifesto da versão; verificar imagem e montagens. "
                + json.dumps(detail, ensure_ascii=True)
            )
    except (OSError, ValueError, TypeError) as exc:
        raise ReleaseIdentityError("O código não corresponde ao manifesto da versão; verificar imagem e montagens.") from exc
    return {**info, "revision": data["revision"], "verified": True}


def write_release_manifest(root: Path, *, revision: str) -> dict:
    """Package a fixed source tree; revision is supplied by the release builder."""
    if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("Indique o SHA Git completo da versão a empacotar.")
    root = Path(root)
    if (root / ".git").exists() or (root / ".git").is_symlink():
        raise ReleaseIdentityError("Não criar manifestos num checkout Git; indique uma cópia de staging sem .git.")
    fingerprint, files = _source_fingerprint(root)
    payload = {"format": 1, "revision": revision, "source_sha256": fingerprint,
               "source_files": len(files), "files": files}
    temporary = root / (".release-" + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(root / MANIFEST_NAME)
    finally:
        temporary.unlink(missing_ok=True)
    return get_release_info(root)
