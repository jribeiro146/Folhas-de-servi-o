"""Conversão local do HTML final da folha de serviço para PDF."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Callable


class LocalPdfError(Exception):
    """Erro ao produzir localmente o PDF final."""


class LocalPdfService:
    """Imprime o HTML final para PDF com um navegador Chromium local."""

    def __init__(self, renderer: Callable[[Path, Path], None] | None = None):
        self._renderer = renderer

    def export_archive_pdf(self, archived_excel_path: str | Path) -> Path:
        source = Path(archived_excel_path).resolve()
        if not source.exists() or not source.is_file():
            raise LocalPdfError(f"Ficheiro Excel arquivado não encontrado: {source}")

        html_path = source.with_name(f"{source.stem}__folha_final.html")
        if not html_path.exists() or not html_path.is_file():
            raise LocalPdfError(f"HTML final da folha não encontrado: {html_path}")

        destination = source.with_name(f"{source.stem}__folha_final.pdf")
        temporary = destination.with_name(
            f".{destination.stem}.{uuid.uuid4().hex[:8]}.tmp.pdf"
        )
        try:
            if self._renderer is not None:
                self._renderer(html_path, temporary)
            else:
                self._render_with_browser(html_path, temporary)
            if not temporary.exists() or not temporary.read_bytes().startswith(b"%PDF-"):
                raise LocalPdfError("O navegador não produziu um PDF válido.")
            os.replace(str(temporary), str(destination))
            return destination
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    @classmethod
    def _render_with_browser(cls, html_path: Path, destination: Path) -> None:
        browser = cls._find_browser()
        configured_temp = os.environ.get("FS_PDF_TEMP_DIR", "").strip() or None
        if configured_temp:
            Path(configured_temp).mkdir(parents=True, exist_ok=True)
        profile_dir = Path(tempfile.mkdtemp(prefix="sp-pdf-", dir=configured_temp))
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        command = [
            str(browser),
            "--headless=new",
            "--disable-background-networking",
            "--disable-breakpad",
            "--disable-component-update",
            "--disable-extensions",
            "--disable-gpu",
            "--disable-sync",
            "--no-first-run",
            "--no-default-browser-check",
            "--allow-file-access-from-files",
            "--no-pdf-header-footer",
            "--print-to-pdf-no-header",
            f"--user-data-dir={profile_dir}",
            f"--print-to-pdf={destination}",
            html_path.as_uri(),
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                creationflags=creation_flags,
            )
        except subprocess.TimeoutExpired as exc:
            raise LocalPdfError("A conversão do HTML para PDF excedeu 60 segundos.") from exc
        finally:
            shutil.rmtree(profile_dir, ignore_errors=True)

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "erro desconhecido").strip()
            raise LocalPdfError(f"O navegador não conseguiu gerar o PDF: {detail[:800]}")

    @staticmethod
    def _find_browser() -> Path:
        configured = os.environ.get("FS_PDF_BROWSER_PATH", "").strip()
        candidates = [
            configured,
            shutil.which("chrome") or "",
            shutil.which("google-chrome") or "",
            shutil.which("chromium") or "",
            shutil.which("msedge") or "",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]
        for value in candidates:
            if value and Path(value).is_file():
                return Path(value)
        raise LocalPdfError(
            "Não foi encontrado Google Chrome ou Microsoft Edge para converter o HTML em PDF."
        )
