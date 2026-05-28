"""Service for rendered document artifacts stored beside each Excel file."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path


class DocumentArtifactError(Exception):
    """Error related to rendered document artifacts."""


class DocumentArtifactService:
    """Stores rendered HTML artifacts beside each Excel file."""

    MOVE_RETRIES = 10
    RETRY_DELAY_SECONDS = 0.2

    def __init__(self, file_path: str | Path):
        self.file_path = Path(file_path)

    def write_html(self, html: str) -> Path:
        path = self._build_html_path(self.file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        return path

    def copy_to(self, destination_file: str | Path) -> None:
        source = self._build_html_path(self.file_path)
        if not source.exists():
            return

        destination = self._build_html_path(Path(destination_file))
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(source), str(destination))

    def move_to(self, destination_file: str | Path) -> None:
        source = self._build_html_path(self.file_path)
        if not source.exists():
            return

        destination = self._build_html_path(Path(destination_file))
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._move_with_retry(source, destination)

    @staticmethod
    def _build_html_path(file_path: Path) -> Path:
        return file_path.with_name(f"{file_path.stem}__folha_final.html")

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
