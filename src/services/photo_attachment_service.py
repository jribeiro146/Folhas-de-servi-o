"""Internal photograph attachments for service-sheet bundles.

Photographs deliberately live outside the document payload.  They are stored as
opaque files in ``Fotografias`` and are therefore never picked up by the Excel,
HTML, PDF or mail-generation paths.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import secrets
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


PHOTO_FOLDER_NAME = "Fotografias"
MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_TOTAL_PHOTO_BYTES = 50 * 1024 * 1024

_MANAGED_PHOTO_RE = re.compile(
    r"^fotografia_(?P<id>[0-9a-f]{64})\.(?P<extension>jpg|png|webp|heic|avif|gif|bmp|tiff|dng)$",
    re.IGNORECASE,
)
_CONTENT_TYPES = {
    "jpg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "heic": "image/heic",
    "avif": "image/avif",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "tiff": "image/tiff",
    "dng": "image/x-adobe-dng",
}


class PhotoAttachmentError(ValueError):
    """Raised when a photograph upload violates the attachment contract."""


@dataclass(frozen=True)
class PreparedPhoto:
    """A validated photograph buffered for an atomic-ish bundle update."""

    id: str
    filename: str
    content_type: str
    data: bytes

    @property
    def size(self) -> int:
        return len(self.data)


class PhotoAttachmentService:
    """Manage internal photos in a service-sheet bundle."""

    def __init__(self, excel_path: Path):
        self.excel_path = Path(excel_path)
        self._is_bundle = self.excel_path.parent.name == self.excel_path.stem
        self.directory = self.excel_path.parent / PHOTO_FOLDER_NAME

    @classmethod
    def prepare_uploads(cls, uploads: Iterable[object]) -> list[PreparedPhoto]:
        prepared: list[PreparedPhoto] = []
        seen_ids: set[str] = set()

        for upload in uploads:
            original_name = str(getattr(upload, "filename", "") or "").strip()
            if not original_name:
                continue

            stream = getattr(upload, "stream", upload)
            read = getattr(stream, "read", None)
            if not callable(read):
                raise PhotoAttachmentError("Não foi possível ler uma das fotografias.")

            data = read(MAX_PHOTO_BYTES + 1)
            if not data:
                raise PhotoAttachmentError(f"A fotografia '{original_name}' está vazia.")
            if len(data) > MAX_PHOTO_BYTES:
                raise PhotoAttachmentError(
                    f"A fotografia '{original_name}' excede o limite de 10 MB."
                )

            extension, content_type = cls._detect_format(data, original_name)
            photo_id = hashlib.sha256(data).hexdigest()
            if photo_id in seen_ids:
                continue
            seen_ids.add(photo_id)
            prepared.append(
                PreparedPhoto(
                    id=photo_id,
                    filename=f"fotografia_{photo_id}.{extension}",
                    content_type=content_type,
                    data=data,
                )
            )

        return prepared

    def list(self) -> list[dict[str, object]]:
        if not self._is_bundle or not self.directory.exists():
            return []

        photos: list[dict[str, object]] = []
        for path in sorted(self.directory.iterdir(), key=lambda item: item.name.lower()):
            match = _MANAGED_PHOTO_RE.fullmatch(path.name)
            if not path.is_file() or match is None:
                continue
            extension = match.group("extension").lower()
            photos.append(
                {
                    "id": match.group("id").lower(),
                    "filename": path.name,
                    "content_type": _CONTENT_TYPES[extension],
                    "size": path.stat().st_size,
                }
            )
        return photos

    def validate_changes(
        self,
        prepared: Sequence[PreparedPhoto],
        removed_ids: Iterable[str],
    ) -> None:
        removed = self._normalize_removed_ids(removed_ids)
        resulting = {
            str(photo["id"]): int(photo["size"])
            for photo in self.list()
            if str(photo["id"]) not in removed
        }
        for photo in prepared:
            resulting[photo.id] = photo.size

        if sum(resulting.values()) > MAX_TOTAL_PHOTO_BYTES:
            raise PhotoAttachmentError(
                "O conjunto de fotografias excede o limite total de 50 MB."
            )

    def apply(
        self,
        prepared: Sequence[PreparedPhoto],
        removed_ids: Iterable[str],
    ) -> list[dict[str, object]]:
        removed = self._normalize_removed_ids(removed_ids)
        if (prepared or removed) and not self._is_bundle:
            raise PhotoAttachmentError("As fotografias só podem ser guardadas num rascunho.")
        self.validate_changes(prepared, removed)

        if self.directory.exists():
            for photo in self.list():
                photo_id = str(photo["id"])
                if photo_id not in removed:
                    continue
                photo_path = self.directory / str(photo["filename"])
                try:
                    self._unlink_if_present(photo_path)
                    self._unlink_if_present(
                        photo_path.with_name(f"{photo_path.name}.graph.json")
                    )
                except OSError as exc:
                    raise PhotoAttachmentError(
                        "Não foi possível remover a fotografia. Tente novamente."
                    ) from exc

        if prepared:
            self.directory.mkdir(parents=True, exist_ok=True)
        existing_by_id = {
            str(photo["id"]): self.directory / str(photo["filename"])
            for photo in self.list()
        }
        for photo in prepared:
            existing = existing_by_id.get(photo.id)
            if existing is not None and existing.exists():
                continue
            destination = self.directory / photo.filename
            temp_path = self.directory / f".{photo.filename}.{secrets.token_hex(6)}.tmp"
            try:
                temp_path.write_bytes(photo.data)
                os.replace(str(temp_path), str(destination))
            finally:
                self._unlink_if_present(temp_path)

        self._remove_empty_directory()
        return self.list()

    def resolve(self, photo_id: str) -> Path | None:
        normalized = str(photo_id or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", normalized):
            return None
        for photo in self.list():
            if photo["id"] == normalized:
                return self.directory / str(photo["filename"])
        return None

    def copy_to(self, destination_excel: Path) -> None:
        """Copy the photo folder while excluding local Graph cache metadata."""
        if not self._is_bundle or not self.directory.exists():
            return
        destination = Path(destination_excel).parent / PHOTO_FOLDER_NAME
        destination.mkdir(parents=True, exist_ok=True)
        for source in self.directory.iterdir():
            if not source.is_file() or self.is_graph_metadata_name(source.name):
                continue
            shutil.copy2(str(source), str(destination / source.name))

    @staticmethod
    def is_managed_filename(filename: str) -> bool:
        return _MANAGED_PHOTO_RE.fullmatch(str(filename or "")) is not None

    @staticmethod
    def is_graph_metadata_name(filename: str) -> bool:
        return filename.endswith(".graph.json") or filename == ".graph_bundle.json"

    @staticmethod
    def content_type_for_path(path: Path) -> str:
        match = _MANAGED_PHOTO_RE.fullmatch(path.name)
        if match:
            return _CONTENT_TYPES[match.group("extension").lower()]
        return mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    @staticmethod
    def _normalize_removed_ids(removed_ids: Iterable[str]) -> set[str]:
        normalized: set[str] = set()
        for value in removed_ids:
            photo_id = str(value or "").strip().lower()
            if not re.fullmatch(r"[0-9a-f]{64}", photo_id):
                raise PhotoAttachmentError("Identificador de fotografia inválido.")
            normalized.add(photo_id)
        return normalized

    @staticmethod
    def _detect_format(data: bytes, original_name: str) -> tuple[str, str]:
        lower_name = original_name.lower()
        if data.startswith(b"\xff\xd8\xff"):
            return "jpg", _CONTENT_TYPES["jpg"]
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "png", _CONTENT_TYPES["png"]
        if data.startswith((b"GIF87a", b"GIF89a")):
            return "gif", _CONTENT_TYPES["gif"]
        if data.startswith(b"BM"):
            return "bmp", _CONTENT_TYPES["bmp"]
        if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "webp", _CONTENT_TYPES["webp"]
        if data.startswith((b"II*\x00", b"MM\x00*")):
            extension = "dng" if lower_name.endswith(".dng") else "tiff"
            return extension, _CONTENT_TYPES[extension]
        if len(data) >= 12 and data[4:8] == b"ftyp":
            brands = {
                data[offset:offset + 4].decode("ascii", errors="ignore").lower()
                for offset in range(8, min(len(data), 64), 4)
            }
            if brands & {"avif", "avis"}:
                return "avif", _CONTENT_TYPES["avif"]
            if brands & {
                "heic", "heix", "hevc", "hevx", "heim", "heis", "mif1", "msf1"
            }:
                return "heic", _CONTENT_TYPES["heic"]

        raise PhotoAttachmentError(
            f"O formato da fotografia '{original_name}' não é suportado."
        )

    @staticmethod
    def _unlink_if_present(path: Path) -> None:
        last_error: OSError | None = None
        for _attempt in range(20):
            try:
                path.unlink()
                return
            except FileNotFoundError:
                return
            except (PermissionError, OSError) as exc:
                last_error = exc
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
                time.sleep(0.1)
        if last_error is not None:
            raise last_error

    def _remove_empty_directory(self) -> None:
        if not self.directory.exists():
            return
        try:
            if not any(self.directory.iterdir()):
                self.directory.rmdir()
        except OSError:
            return
