"""Conversão local do HTML final da folha de serviço para PDF."""

from __future__ import annotations

import os
import signal
import shutil
import subprocess
import tempfile
import time
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
        configured_temp = os.environ.get("FS_PDF_TEMP_DIR", "").strip() or None
        working_root = Path(configured_temp).resolve() if configured_temp else Path(tempfile.gettempdir())
        working_root.mkdir(parents=True, exist_ok=True)
        temporary = working_root / f"sp-pdf-{uuid.uuid4().hex}.pdf"
        staged_destination = destination.with_name(
            f".{destination.stem}.{uuid.uuid4().hex[:8]}.tmp.pdf"
        )
        try:
            if self._renderer is not None:
                self._renderer(html_path, temporary)
            else:
                self._render_with_browser(html_path, temporary)
            if not temporary.exists() or not temporary.read_bytes().startswith(b"%PDF-"):
                raise LocalPdfError("O navegador não produziu um PDF válido.")
            # O Chromium nunca escreve diretamente na pasta OneDrive. O PDF completo
            # é copiado para um staging adjacente e só depois publicado atomicamente.
            shutil.copyfile(temporary, staged_destination)
            if not staged_destination.read_bytes().startswith(b"%PDF-"):
                raise LocalPdfError("Não foi possível publicar um PDF válido no arquivo.")
            os.replace(str(staged_destination), str(destination))
            return destination
        finally:
            for candidate in (temporary, staged_destination):
                try:
                    candidate.unlink()
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
            "--disable-component-update",
            "--disable-extensions",
            "--disable-gpu",
            "--disable-dev-shm-usage",
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
        if os.environ.get("FS_PDF_BROWSER_NO_SANDBOX", "").strip().casefold() in {
            "1",
            "true",
            "yes",
            "sim",
            "on",
        }:
            command.insert(1, "--no-sandbox")
        process = None
        windows_job = None
        return_code = None
        detail = ""
        try:
            # Pipes podem ficar abertos nos subprocessos Chromium depois de o processo
            # principal terminar, bloqueando indefinidamente o tratamento de timeout.
            # Um ficheiro temporário preserva o diagnóstico sem essa dependência.
            with tempfile.TemporaryFile(mode="w+b") as browser_output:
                process = subprocess.Popen(
                    command,
                    stdout=browser_output,
                    stderr=subprocess.STDOUT,
                    creationflags=creation_flags,
                    start_new_session=os.name != "nt",
                )
                windows_job = cls._create_windows_process_job(process)
                try:
                    return_code = process.wait(timeout=60)
                except subprocess.TimeoutExpired as exc:
                    windows_job = cls._terminate_browser_process(process, windows_job)
                    raise LocalPdfError(
                        "A conversão do HTML para PDF excedeu 60 segundos."
                    ) from exc
                except BaseException:
                    windows_job = cls._terminate_browser_process(process, windows_job)
                    raise

                if return_code == 0:
                    cls._wait_for_pdf_file(destination, timeout=10)

                cls._close_windows_process_job(windows_job)
                windows_job = None

                browser_output.seek(0)
                detail = browser_output.read().decode("utf-8", errors="replace").strip()
        finally:
            if process is not None and process.poll() is None:
                windows_job = cls._terminate_browser_process(process, windows_job)
            cls._close_windows_process_job(windows_job)
            shutil.rmtree(profile_dir, ignore_errors=True)

        if return_code != 0:
            raise LocalPdfError(
                f"O navegador não conseguiu gerar o PDF: {(detail or 'erro desconhecido')[:800]}"
            )

    @staticmethod
    def _wait_for_pdf_file(destination: Path, *, timeout: float) -> None:
        """Mantém os filhos Chromium vivos até o PDF estar completo no disco."""
        deadline = time.monotonic() + timeout
        previous_size = -1
        stable_checks = 0
        while time.monotonic() < deadline:
            try:
                size = destination.stat().st_size
                with destination.open("rb") as pdf_file:
                    valid_header = pdf_file.read(5) == b"%PDF-"
            except (FileNotFoundError, PermissionError, OSError):
                size = 0
                valid_header = False
            if valid_header and size > 5:
                stable_checks = stable_checks + 1 if size == previous_size else 0
                if stable_checks >= 2:
                    return
                previous_size = size
            time.sleep(0.1)
        raise LocalPdfError("O navegador terminou sem produzir um PDF válido.")

    @staticmethod
    def _create_windows_process_job(process: subprocess.Popen) -> object | None:
        """Liga o Chromium a um Job Object que termina os filhos com o servidor."""
        if os.name != "nt":
            return None

        job = None
        try:
            import win32api
            import win32job

            job = win32job.CreateJobObject(None, "")
            limits = win32job.QueryInformationJobObject(
                job,
                win32job.JobObjectExtendedLimitInformation,
            )
            limits["BasicLimitInformation"]["LimitFlags"] |= (
                win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            win32job.SetInformationJobObject(
                job,
                win32job.JobObjectExtendedLimitInformation,
                limits,
            )
            win32job.AssignProcessToJobObject(job, int(process._handle))
            return job
        except Exception:
            if job is not None:
                try:
                    win32api.CloseHandle(job)
                except Exception:
                    pass
            return None

    @classmethod
    def _terminate_browser_process(
        cls,
        process: subprocess.Popen,
        windows_job: object | None,
    ) -> object | None:
        if windows_job is not None:
            cls._close_windows_process_job(windows_job)
            windows_job = None
        elif process.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (AttributeError, OSError, ProcessLookupError):
                    process.kill()

        try:
            process.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            pass
        return windows_job

    @staticmethod
    def _close_windows_process_job(windows_job: object | None) -> None:
        if windows_job is None or os.name != "nt":
            return
        try:
            import win32api

            win32api.CloseHandle(windows_job)
        except Exception:
            pass

    @staticmethod
    def _find_browser() -> Path:
        configured = os.environ.get("FS_PDF_BROWSER_PATH", "").strip()
        candidates = [
            configured,
            shutil.which("msedge") or "",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            shutil.which("chrome") or "",
            shutil.which("google-chrome") or "",
            shutil.which("chromium") or "",
            shutil.which("chromium-browser") or "",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        ]
        for value in candidates:
            if value and Path(value).is_file():
                return Path(value)
        raise LocalPdfError(
            "Não foi encontrado Chrome, Chromium ou Microsoft Edge para converter o HTML em PDF."
        )
