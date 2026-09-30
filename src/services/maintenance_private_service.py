"""Private SADI draft data; never part of the SharePoint document bundle."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
from pathlib import Path


class MaintenancePrivateService:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, document_id: str) -> Path:
        key = hashlib.sha256(document_id.encode("utf-8")).hexdigest()[:32]
        return self.root / f"{key}.json"

    def read(self, document_id: str) -> list[dict]:
        path = self._path(document_id)
        if not path.is_file():
            return []
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, list):
            raise ValueError("Dados privados SADI inválidos.")
        return value

    def write(self, document_id: str, sites: list[dict]) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self._path(document_id)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(sites, stream, ensure_ascii=False)
            if os.name != "nt":
                temporary.chmod(0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def publish_bundle(self, staged: Path, document: dict, renderer,
                       document_id: str, operation_id: str, archived_name: str) -> str:
        """Finish every PDF in persistent private storage before archive commit."""
        from src.services.maintenance_artifact_service import build_private_maintenance_bundle

        fingerprint = hashlib.sha256(
            json.dumps(document, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        key = hashlib.sha256(
            f"{document_id}:{operation_id}:{fingerprint}".encode("utf-8")
        ).hexdigest()[:32]
        # mkdir(parents=True, mode=...) applies the mode only to the leaf.
        # First-time finalization may precede any private draft write.
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        archive_root = self.root / "pdf"
        archive_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination = archive_root / key
        metadata = {"operation_id": operation_id, "archived_name": archived_name}
        if destination.exists():
            if self.bundle(key)[0] != metadata:
                raise ValueError("Conjunto privado SADI incompatível com esta operação.")
            return key

        temporary = archive_root / f".t-{secrets.token_hex(4)}"
        temporary.mkdir(mode=0o700)
        try:
            entries = build_private_maintenance_bundle(staged, document, renderer, temporary)
            if not entries or any(
                not (temporary / item["name"]).read_bytes().startswith(b"%PDF-")
                for item in entries
            ):
                raise ValueError("Conjunto SADI incompleto.")
            (temporary / "metadata.json").write_text(
                json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
            )
            if os.name != "nt":
                for item in temporary.iterdir():
                    item.chmod(0o600)
            os.rename(temporary, destination)
            return key
        finally:
            if temporary.exists() and temporary.resolve().parent == archive_root.resolve():
                shutil.rmtree(temporary)

    def bundle(self, key: str) -> tuple[dict, list[dict], Path]:
        if len(key) != 32 or any(char not in "0123456789abcdef" for char in key):
            raise FileNotFoundError("Conjunto SADI inválido.")
        directory = self.root / "pdf" / key
        metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        entries = json.loads((directory / "maintenance-manifest.json").read_text(encoding="utf-8"))
        if not isinstance(entries, list) or not entries or any(
            Path(item["name"]).name != item["name"]
            or not item["name"].endswith(".pdf")
            or not (directory / item["name"]).is_file()
            for item in entries
        ):
            raise FileNotFoundError("Conjunto SADI incompleto.")
        return metadata, entries, directory
