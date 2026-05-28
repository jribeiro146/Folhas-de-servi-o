"""
Folhas de Servico - Servico de gestao de ficheiros.

Lista e valida ficheiros Excel na pasta de folhas ativas.
Nao realiza operacoes de arquivo - isso e responsabilidade do archive_service.
"""

from __future__ import annotations

import datetime as dt
import re
import shutil
from pathlib import Path

from src.document_schema import document_from_excel_and_extra, get_technician_initials
from src.config import EXCEL_ACTIVAS_DIR, EXCEL_EXTENSION
from src.services.document_artifact_service import DocumentArtifactService
from src.services.document_data_service import DocumentDataService
from src.services.excel_service import ExcelFileLockedError, ExcelService, ExcelValidationError
from src.services.signature_service import SignatureService


DRAFT_NAME_RE = re.compile(r"^.+_\d{4}-\d{2}-\d{2}_.+$")
ARCHIVED_MARKER_NAME = ".fs_archived"
ARCHIVED_SINGLE_SUFFIX = ".fs_archived"


class FileService:
    """Servico para listar e validar ficheiros Excel disponiveis."""

    def __init__(self, directory: Path | None = None):
        self.directory = directory or EXCEL_ACTIVAS_DIR
        self._entry_cache: dict[Path, tuple[tuple[int, int, int], dict]] = {}

    def list_excel_files(self) -> list[Path]:
        if not self.directory.exists():
            return []

        excel_files: list[Path] = []

        for item in self.directory.iterdir():
            if item.is_file() and self._is_excel_file(item):
                if self._is_archived_single_file(item):
                    continue
                excel_files.append(item)
                continue

            if item.is_dir():
                if self._is_archived_bundle_dir(item):
                    continue
                bundle_excel = self._find_excel_in_bundle(item)
                if bundle_excel is not None:
                    excel_files.append(bundle_excel)

        return sorted(excel_files, key=lambda path: path.stem.lower())

    def list_valid_files(self) -> list[dict]:
        results: list[dict] = []

        for file_path in self.list_excel_files():
            cache_key = self._build_cache_key(file_path)
            cached = self._entry_cache.get(file_path)
            if cached and cached[0] == cache_key:
                results.append(self._clone_entry(cached[1]))
                continue

            entry = {
                "path": file_path,
                "name": file_path.stem,
                "valid": True,
                "status": "in_progress" if self.is_draft_file(file_path) else "ready",
                "error": None,
                "modified_at": file_path.stat().st_mtime,
                "summary": self._build_summary(file_path),
            }

            try:
                excel_form_data = ExcelService(file_path).read_link_as_form_data()
                entry["summary"] = self._build_summary(file_path, excel_form_data=excel_form_data)
            except ExcelFileLockedError as exc:
                entry["valid"] = False
                entry["status"] = "locked"
                entry["error"] = str(exc)
            except ExcelValidationError as exc:
                entry["valid"] = False
                entry["status"] = "invalid"
                entry["error"] = str(exc)
            except Exception as exc:
                entry["valid"] = False
                entry["status"] = "invalid"
                entry["error"] = f"Erro inesperado: {exc}"

            self._entry_cache[file_path] = (cache_key, self._clone_entry(entry))
            results.append(entry)

        return results

    def invalidate_cache(self, file_path: Path | None = None) -> None:
        if file_path is None:
            self._entry_cache.clear()
            return

        self._entry_cache.pop(Path(file_path), None)

    def _build_summary(
        self,
        file_path: Path,
        *,
        excel_form_data: dict | None = None,
    ) -> dict[str, str]:
        try:
            extra_data = DocumentDataService(file_path).read()
        except Exception:
            extra_data = {}

        document = document_from_excel_and_extra(excel_form_data or {}, extra_data)

        contact_name = str(document.get("site_contact") or "").strip()
        fallback_contact_name = str(document.get("requested_by") or "").strip()
        phone_value = str(document.get("site_phone") or "").strip()
        fallback_phone_value = str(document.get("customer_phone") or "").strip()

        return {
            "customer_name": str(document.get("customer_name") or "").strip(),
            "contact_label": "Contacto" if contact_name else ("Pedido por" if fallback_contact_name else "Pessoa"),
            "contact_name": contact_name or fallback_contact_name,
            "address": str(document.get("address") or "").strip(),
            "phone_label": "Telefone",
            "phone": phone_value or fallback_phone_value,
        }

    def get_file_by_name(self, name: str) -> Path | None:
        target = self.directory / f"{name}{EXCEL_EXTENSION}"
        if target.exists() and target.is_file() and not self._is_archived_single_file(target):
            return target

        bundle_dir = self.directory / name
        if self._is_archived_bundle_dir(bundle_dir):
            return None

        bundle_target = bundle_dir / f"{name}{EXCEL_EXTENSION}"
        if bundle_target.exists() and bundle_target.is_file():
            return bundle_target

        if bundle_dir.exists() and bundle_dir.is_dir():
            return self._find_excel_in_bundle(bundle_dir)

        return None

    def is_draft_file(self, file_path: Path) -> bool:
        return bool(DRAFT_NAME_RE.match(file_path.stem))

    def create_draft_copy(self, file_path: Path, technician_name: str | None) -> Path:
        if not file_path.exists() or not file_path.is_file():
            raise FileNotFoundError(f"Ficheiro Excel nao encontrado: {file_path}")

        if self.is_draft_file(file_path):
            return file_path

        draft_name = self._build_draft_name(file_path.stem, technician_name)
        draft_dir = self._resolve_conflict(self.directory / draft_name)
        draft_dir.mkdir(parents=True, exist_ok=True)
        draft_path = draft_dir / f"{draft_dir.name}{file_path.suffix}"
        shutil.copy2(str(file_path), str(draft_path))
        DocumentArtifactService(file_path).copy_to(draft_path)
        DocumentDataService(file_path).copy_to(draft_path)
        SignatureService(file_path).copy_to(draft_path)
        return draft_path

    def _build_draft_name(self, base_name: str, technician_name: str | None) -> str:
        current_date = dt.date.today().strftime("%Y-%m-%d")
        safe_name = self._sanitize_technician_name(technician_name)
        return f"{base_name}_{current_date}_{safe_name}"

    @staticmethod
    def _sanitize_technician_name(technician_name: str | None) -> str:
        initials = get_technician_initials(technician_name)
        if not initials:
            return "Sem_Tecnico"
        return initials

    @staticmethod
    def _resolve_conflict(path: Path) -> Path:
        counter = 1
        candidate = path

        while candidate.exists():
            if path.suffix:
                candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
            else:
                candidate = path.with_name(f"{path.name}_{counter}")
            counter += 1

        return candidate

    @staticmethod
    def _is_excel_file(path: Path) -> bool:
        return (
            path.is_file()
            and path.suffix.lower() == EXCEL_EXTENSION
            and not path.name.startswith("~$")
        )

    def _find_excel_in_bundle(self, directory: Path) -> Path | None:
        if FileService._is_archived_bundle_dir(directory):
            return None

        exact_match = directory / f"{directory.name}{EXCEL_EXTENSION}"
        if self._is_excel_file(exact_match):
            return exact_match

        candidates = sorted(
            file_path for file_path in directory.iterdir()
            if self._is_excel_file(file_path)
        )
        return candidates[0] if candidates else None

    @staticmethod
    def _build_cache_key(file_path: Path) -> tuple[int, int, int]:
        file_stat = file_path.stat()
        document_path = file_path.with_name(f"{file_path.stem}__documento.json")
        document_mtime = document_path.stat().st_mtime_ns if document_path.exists() else 0
        return (file_stat.st_mtime_ns, file_stat.st_size, document_mtime)

    @staticmethod
    def _clone_entry(entry: dict) -> dict:
        clone = dict(entry)
        clone["summary"] = dict(entry.get("summary") or {})
        return clone

    @staticmethod
    def _is_archived_bundle_dir(path: Path) -> bool:
        return path.is_dir() and (path / ARCHIVED_MARKER_NAME).exists()

    @staticmethod
    def _is_archived_single_file(path: Path) -> bool:
        return path.with_name(f"{path.name}{ARCHIVED_SINGLE_SUFFIX}").exists()
