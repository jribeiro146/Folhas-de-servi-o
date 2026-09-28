"""Prepare complete archive bundles while retaining the editable source."""

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path

from src.services import archive_service as archive_module
from src.services.file_mutex import file_mutex


class FinalizationService:
    MARKER = ".fs-finalization.json"

    def __init__(self, archive_service, staging_root: Path):
        self.archive = archive_service
        self.staging_root = Path(staging_root)

    def prepare(self, source: Path, operation_id: str) -> tuple[Path, Path]:
        archive_root = archive_module.EXCEL_ARQUIVADAS_DIR
        archive_root.mkdir(parents=True, exist_ok=True)
        with file_mutex(archive_root / ".fs-locks" / "reserve"):
            destination = self.archive._resolve_conflict(archive_root / source.stem)
        # Every attempt gets fresh staging; a failed attempt cannot contaminate
        # a later retry with old photographs or partial sidecars.
        identifier = hashlib.sha256(operation_id.encode()).hexdigest()[:12]
        attempt = self.staging_root / (identifier + "-" + uuid.uuid4().hex[:8])
        bundle = attempt / destination.name
        bundle.mkdir(parents=True)
        staged = bundle / (destination.name + source.suffix)
        try:
            shutil.copy2(source, staged)
            self.archive._copy_sidecars(source, staged)
            (bundle / self.MARKER).write_text(json.dumps({"operation_id": operation_id}), encoding="utf-8")
        except Exception:
            self.discard(staged)
            raise
        return staged, destination / staged.name

    def publish(self, staged: Path, target: Path, operation_id: str) -> Path:
        if target.parent.exists():
            if self.owns(target, operation_id):
                return target
            raise FileExistsError("Já existe um arquivo com este nome; a origem foi preservada.")
        # Copy across volumes first, then rename a complete directory on the
        # archive volume. rename never replaces a pre-existing archive.
        temporary = target.parent.parent / (".fs-stage-" + uuid.uuid4().hex[:12])
        try:
            shutil.copytree(staged.parent, temporary)
            os.rename(temporary, target.parent)
        finally:
            if temporary.exists() and temporary.parent.resolve() == target.parent.parent.resolve():
                shutil.rmtree(temporary)
        return target

    def owns(self, target: Path, operation_id: str) -> bool:
        try:
            return target.is_file() and json.loads(
                (target.parent / self.MARKER).read_text(encoding="utf-8")
            ).get("operation_id") == operation_id
        except (OSError, ValueError):
            return False

    def retire_source(self, source: Path) -> None:
        if not source.exists():
            return
        self.archive._mark_archived(source)
        if self.archive._is_bundle_file(source):
            self.archive._cleanup_source_bundle(source.parent)
        else:
            self.archive._cleanup_source_file(source)

    def discard(self, staged: Path) -> None:
        attempt = staged.parent.parent.resolve()
        root = self.staging_root.resolve()
        if attempt != root and attempt.is_relative_to(root) and attempt.exists():
            shutil.rmtree(attempt)
