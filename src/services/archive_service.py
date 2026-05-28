"""
Folhas de Serviço - Serviço de arquivo.

Move ficheiros Excel da pasta Activas para Arquivadas ou Canceladas.
Nunca apaga ficheiros - apenas move.
"""

from pathlib import Path
import gc
import os
import shutil
import stat
import time

from src.config import EXCEL_ARQUIVADAS_DIR, EXCEL_CANCELADAS_DIR
from src.services.document_artifact_service import DocumentArtifactService
from src.services.document_data_service import DocumentDataService
from src.services.signature_service import SignatureService


class ArchiveError(Exception):
    """Erro ao arquivar ficheiro."""
    pass


class ArchiveService:
    """Serviço para mover ficheiros Excel entre pastas de estado."""

    MOVE_RETRIES = 20
    RETRY_DELAY_SECONDS = 0.5
    ARCHIVED_MARKER_NAME = ".fs_archived"
    ARCHIVED_SINGLE_SUFFIX = ".fs_archived"

    def archive(self, file_path: Path) -> Path:
        return self._move_file(file_path, EXCEL_ARQUIVADAS_DIR)

    def cancel(self, file_path: Path) -> Path:
        return self._move_file(file_path, EXCEL_CANCELADAS_DIR)

    def _move_file(self, file_path: Path, dest_dir: Path) -> Path:
        if not file_path.exists():
            raise ArchiveError(f"Ficheiro não encontrado: {file_path}")

        dest_dir.mkdir(parents=True, exist_ok=True)

        try:
            if self._is_bundle_file(file_path):
                return self._move_bundle(file_path, dest_dir)

            return self._move_single_file(file_path, dest_dir)
        except Exception as e:
            raise ArchiveError(
                f"Erro ao mover '{file_path.name}' para '{dest_dir}': {e}"
            ) from e

    def _move_single_file(self, file_path: Path, dest_dir: Path) -> Path:
        bundle_dir = self._resolve_conflict(dest_dir / file_path.stem)
        bundle_dir.mkdir(parents=True, exist_ok=True)
        dest_path = bundle_dir / f"{bundle_dir.name}{file_path.suffix}"

        self._copy_with_retry(file_path, dest_path)
        self._copy_sidecars(file_path, dest_path)
        self._mark_archived(file_path)
        self._cleanup_source_file(file_path)
        return dest_path

    def _move_bundle(self, file_path: Path, dest_dir: Path) -> Path:
        source_dir = file_path.parent
        target_dir = self._resolve_conflict(dest_dir / source_dir.name)
        target_dir.mkdir(parents=True, exist_ok=False)

        archived_excel = target_dir / f"{target_dir.name}{file_path.suffix}"
        self._copy_with_retry(file_path, archived_excel)
        self._copy_sidecars(file_path, archived_excel)
        self._mark_archived(file_path)
        self._cleanup_source_bundle(source_dir)
        return archived_excel

    @staticmethod
    def _resolve_conflict(dest_path: Path) -> Path:
        stem = dest_path.stem
        suffix = dest_path.suffix
        parent = dest_path.parent

        counter = 1
        while dest_path.exists():
            dest_path = parent / f"{stem}_{counter}{suffix}"
            counter += 1

        return dest_path

    def _move_with_retry(self, source: Path, destination: Path) -> None:
        last_error: Exception | None = None

        for _ in range(self.MOVE_RETRIES):
            try:
                os.replace(str(source), str(destination))
                return
            except FileNotFoundError:
                raise
            except (PermissionError, OSError) as exc:
                last_error = exc
                time.sleep(self.RETRY_DELAY_SECONDS)

        for _ in range(self.MOVE_RETRIES):
            try:
                shutil.copy2(str(source), str(destination))
                self._unlink_with_retry(source)
                return
            except FileNotFoundError:
                raise
            except (PermissionError, OSError) as exc:
                last_error = exc
                time.sleep(self.RETRY_DELAY_SECONDS)

        if last_error is not None:
            raise last_error

    def _copy_with_retry(self, source: Path, destination: Path) -> None:
        last_error: Exception | None = None

        for _ in range(self.MOVE_RETRIES):
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(source), str(destination))
                return
            except FileNotFoundError:
                raise
            except (PermissionError, OSError) as exc:
                last_error = exc
                time.sleep(self.RETRY_DELAY_SECONDS)

        if last_error is not None:
            raise last_error

    @staticmethod
    def _copy_sidecars(source: Path, destination: Path) -> None:
        DocumentArtifactService(source).copy_to(destination)
        DocumentDataService(source).copy_to(destination)
        SignatureService(source).copy_to(destination)

    def _cleanup_source_file(self, file_path: Path) -> None:
        try:
            self._make_writable(file_path)
            file_path.unlink()
        except (PermissionError, OSError):
            return

        for sidecar in self._source_sidecar_paths(file_path):
            try:
                self._make_writable(sidecar)
                sidecar.unlink()
            except FileNotFoundError:
                continue
            except (PermissionError, OSError):
                continue

    def _cleanup_source_bundle(self, source_dir: Path) -> None:
        try:
            shutil.rmtree(str(source_dir), onexc=self._handle_remove_error)
        except FileNotFoundError:
            return
        except (PermissionError, OSError):
            try:
                (source_dir / self.ARCHIVED_MARKER_NAME).write_text("archived", encoding="utf-8")
            except OSError:
                pass
            return

    def _mark_archived(self, file_path: Path) -> None:
        marker = self._marker_path(file_path)
        try:
            marker.write_text("archived", encoding="utf-8")
        except OSError:
            pass

    @classmethod
    def _marker_path(cls, file_path: Path) -> Path:
        if cls._is_bundle_file(file_path):
            return file_path.parent / cls.ARCHIVED_MARKER_NAME
        return file_path.with_name(f"{file_path.name}{cls.ARCHIVED_SINGLE_SUFFIX}")

    @staticmethod
    def _source_sidecar_paths(file_path: Path) -> list[Path]:
        return [
            file_path.with_name(f"{file_path.stem}__folha_final.html"),
            file_path.with_name(f"{file_path.stem}__documento.json"),
            file_path.with_name(f"{file_path.stem}__assinatura_cliente.png"),
            file_path.with_name(f"{file_path.stem}__assinatura_tecnico.png"),
        ]

    def _move_path_with_retry(self, source: Path, destination: Path) -> Path:
        last_error: Exception | None = None

        for _ in range(self.MOVE_RETRIES):
            try:
                os.replace(str(source), str(destination))
                return destination
            except FileNotFoundError:
                raise
            except (PermissionError, OSError) as exc:
                last_error = exc
                destination = self._prepare_directory_destination(destination)
                time.sleep(self.RETRY_DELAY_SECONDS)

        for _ in range(self.MOVE_RETRIES):
            try:
                destination = self._prepare_directory_destination(destination)
                shutil.copytree(str(source), str(destination))
                self._remove_path_with_retry(source)
                return destination
            except FileNotFoundError:
                raise
            except (PermissionError, OSError) as exc:
                last_error = exc
                destination = self._prepare_directory_destination(destination)
                time.sleep(self.RETRY_DELAY_SECONDS)

        if last_error is not None:
            raise last_error

        return destination

    def _prepare_directory_destination(self, destination: Path) -> Path:
        if not destination.exists():
            return destination

        if destination.is_dir():
            try:
                if not any(destination.iterdir()):
                    destination.rmdir()
                    return destination
            except OSError:
                pass

        return self._resolve_conflict(destination)

    def _unlink_with_retry(self, file_path: Path) -> None:
        last_error: Exception | None = None

        for _ in range(self.MOVE_RETRIES):
            try:
                gc.collect()
                self._make_writable(file_path)
                file_path.unlink()
                return
            except FileNotFoundError:
                return
            except (PermissionError, OSError) as exc:
                last_error = exc
                time.sleep(self.RETRY_DELAY_SECONDS)

        if last_error is not None:
            raise last_error

    def _remove_path_with_retry(self, path: Path) -> None:
        last_error: Exception | None = None

        for _ in range(self.MOVE_RETRIES):
            try:
                gc.collect()
                shutil.rmtree(str(path), onexc=self._handle_remove_error)
                return
            except FileNotFoundError:
                return
            except (PermissionError, OSError) as exc:
                last_error = exc
                time.sleep(self.RETRY_DELAY_SECONDS)

        if last_error is not None:
            raise last_error

    @staticmethod
    def _make_writable(path: Path | str) -> None:
        try:
            os.chmod(str(path), stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass

    @staticmethod
    def _handle_remove_error(function, path, exc_info) -> None:
        ArchiveService._make_writable(path)
        function(path)

    @staticmethod
    def _is_bundle_file(file_path: Path) -> bool:
        return file_path.parent.is_dir() and file_path.parent.name == file_path.stem
