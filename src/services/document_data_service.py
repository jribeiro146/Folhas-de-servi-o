"""Service for reading and writing structured document data sidecars."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import time
from pathlib import Path
from typing import Any


class DocumentDataError(Exception):
    """Error related to document sidecar data."""


class DocumentDataService:
    """Stores structured document payloads beside each Excel file."""

    MOVE_RETRIES = 10
    RETRY_DELAY_SECONDS = 0.2

    def __init__(self, file_path: str | Path):
        self.file_path = Path(file_path)

    def read(self) -> dict[str, Any]:
        path = self._build_document_path(self.file_path)
        if not path.exists() or not path.is_file():
            return {}

        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DocumentDataError(f"Documento JSON invalido: {path.name}") from exc

    def write(self, payload: dict[str, Any]) -> None:
        path = self._build_document_path(self.file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
        try:
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(str(temp_path), str(path))
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass

    def copy_to(self, destination_file: str | Path) -> None:
        source = self._build_document_path(self.file_path)
        if not source.exists():
            return

        destination = self._build_document_path(Path(destination_file))
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(source), str(destination))

    def move_to(self, destination_file: str | Path) -> None:
        source = self._build_document_path(self.file_path)
        if not source.exists():
            return

        destination = self._build_document_path(Path(destination_file))
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._move_with_retry(source, destination)

    @staticmethod
    def _build_document_path(file_path: Path) -> Path:
        return file_path.with_name(f"{file_path.stem}__documento.json")

    def _move_with_retry(self, source: Path, destination: Path) -> None:
        last_error: Exception | None = None

        for _ in range(self.MOVE_RETRIES):
            try:
                os.replace(str(source), str(destination))
                return
            except FileNotFoundError:
                return
            except (PermissionError, OSError) as exc:
                last_error = exc
                time.sleep(self.RETRY_DELAY_SECONDS)

        for _ in range(self.MOVE_RETRIES):
            try:
                shutil.copy2(str(source), str(destination))
                source.unlink()
                return
            except FileNotFoundError:
                return
            except (PermissionError, OSError) as exc:
                last_error = exc
                time.sleep(self.RETRY_DELAY_SECONDS)

        if last_error is not None:
            raise last_error
