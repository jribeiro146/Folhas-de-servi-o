"""Identity of the code and declared dependencies, without importing the app."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path

MANIFEST_NAME = "release-manifest.json"


class ReleaseIdentityError(RuntimeError):
    """The running files do not match the packaged release."""


def _source_fingerprint(root: Path) -> tuple[str, int]:
    if not (root / "src").is_dir():
        raise ReleaseIdentityError("Pasta de código da aplicação em falta.")
    paths = [p for p in (root / "src").rglob("*") if p.is_file()
             and "__pycache__" not in p.parts and p.suffix != ".pyc"]
    paths += [root / name for name in ("passenger_wsgi.py", "requirements-server.txt", "requirements-smoke.txt")
              if (root / name).is_file()]
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda p: p.relative_to(root).as_posix()):
        if path.is_symlink():
            raise ReleaseIdentityError("O pacote não pode conter ligações simbólicas no código.")
        data = path.read_bytes()
        if path.suffix in {".py", ".js", ".css", ".html", ".json", ".svg", ".txt"}:
            data = data.removeprefix(b"\xef\xbb\xbf").replace(b"\r\n", b"\n")
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(data).digest())
    return digest.hexdigest(), len(paths)


def get_release_info(root: Path | None = None) -> dict:
    root = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    fingerprint, count = _source_fingerprint(root)
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
                or data.get("source_sha256") != fingerprint or data.get("source_files") != count):
            raise ValueError("Invalid release manifest")
    except (OSError, ValueError, TypeError) as exc:
        raise ReleaseIdentityError("O código não corresponde ao manifesto da versão; verificar imagem e montagens.") from exc
    return {**info, "revision": data["revision"], "verified": True}


def write_release_manifest(root: Path, *, revision: str) -> dict:
    """Package a fixed source tree; revision is supplied by the release builder."""
    if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("Indique o SHA Git completo da versão a empacotar.")
    root = Path(root)
    fingerprint, count = _source_fingerprint(root)
    payload = {"format": 1, "revision": revision, "source_sha256": fingerprint, "source_files": count}
    temporary = root / (".release-" + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(root / MANIFEST_NAME)
    finally:
        temporary.unlink(missing_ok=True)
    return get_release_info(root)
