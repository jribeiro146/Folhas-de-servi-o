"""Serviço para guardar e ler assinaturas associadas a cada folha Excel."""

from __future__ import annotations

import base64
import binascii
import os
import shutil
import time
from pathlib import Path

CLIENT_SIGNATURE_LABEL = "Assinatura Cliente"
TECHNICIAN_SIGNATURE_LABEL = "Assinatura Técnico"
DATA_URL_PREFIX = "data:image/png;base64,"

ACTIVE_SIGNATURE_FIELD_TO_SUFFIX = {
    CLIENT_SIGNATURE_LABEL: "cliente",
}
LEGACY_SIGNATURE_FIELD_TO_SUFFIX = {
    TECHNICIAN_SIGNATURE_LABEL: "tecnico",
}
ALL_SIGNATURE_FIELD_TO_SUFFIX = {
    **ACTIVE_SIGNATURE_FIELD_TO_SUFFIX,
    **LEGACY_SIGNATURE_FIELD_TO_SUFFIX,
}


class SignatureError(Exception):
    """Erro relacionado com leitura ou escrita de assinaturas."""


class SignatureService:
    """Gere assinaturas como ficheiros PNG sidecar do Excel."""

    MOVE_RETRIES = 10
    RETRY_DELAY_SECONDS = 0.2

    def __init__(self, file_path: str | Path):
        self.file_path = Path(file_path)

    @classmethod
    def required_labels(cls) -> list[str]:
        return list(ACTIVE_SIGNATURE_FIELD_TO_SUFFIX.keys())

    def resolve_from_form_data(self, form_data: dict[str, object]) -> dict[str, str | None]:
        signatures = self.read_as_data_urls()

        for label in ACTIVE_SIGNATURE_FIELD_TO_SUFFIX:
            if label in form_data:
                signatures[label] = self.normalize_value(form_data.get(label))

        return signatures

    def read_as_data_urls(self) -> dict[str, str | None]:
        signatures: dict[str, str | None] = {}
        for label, suffix in ACTIVE_SIGNATURE_FIELD_TO_SUFFIX.items():
            path = self._build_signature_path(self.file_path, suffix)
            signatures[label] = self._read_signature(path)
        return signatures

    def save_from_form_data(self, form_data: dict[str, object]) -> None:
        for label, suffix in ACTIVE_SIGNATURE_FIELD_TO_SUFFIX.items():
            if label not in form_data:
                continue

            value = form_data.get(label)
            path = self._build_signature_path(self.file_path, suffix)
            self._write_signature(path, value)

        for suffix in LEGACY_SIGNATURE_FIELD_TO_SUFFIX.values():
            self._write_signature(self._build_signature_path(self.file_path, suffix), None)

    def copy_to(self, destination_file: str | Path) -> None:
        destination_path = Path(destination_file)
        for suffix in ALL_SIGNATURE_FIELD_TO_SUFFIX.values():
            source = self._build_signature_path(self.file_path, suffix)
            if not source.exists():
                continue

            destination = self._build_signature_path(destination_path, suffix)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source), str(destination))

    def move_to(self, destination_file: str | Path) -> None:
        destination_path = Path(destination_file)
        for suffix in ALL_SIGNATURE_FIELD_TO_SUFFIX.values():
            source = self._build_signature_path(self.file_path, suffix)
            if not source.exists():
                continue

            destination = self._build_signature_path(destination_path, suffix)
            destination.parent.mkdir(parents=True, exist_ok=True)
            self._move_with_retry(source, destination)

    def _read_signature(self, path: Path) -> str | None:
        if not path.exists() or not path.is_file():
            return None

        content = path.read_bytes()
        encoded = base64.b64encode(content).decode("ascii")
        return f"{DATA_URL_PREFIX}{encoded}"

    def _write_signature(self, path: Path, value: object) -> None:
        normalized = self.normalize_value(value)
        if not normalized:
            if path.exists():
                path.unlink()
            return

        raw_bytes = self.decode_data_url(normalized)

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw_bytes)

    @staticmethod
    def normalize_value(value: object) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None

    @staticmethod
    def decode_data_url(value: object) -> bytes:
        normalized = SignatureService.normalize_value(value)
        if not normalized or not normalized.startswith(DATA_URL_PREFIX):
            raise SignatureError("Formato de assinatura inválido.")

        try:
            return base64.b64decode(normalized[len(DATA_URL_PREFIX):], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise SignatureError("Não foi possível validar a assinatura.") from exc

    @staticmethod
    def _build_signature_path(file_path: Path, suffix: str) -> Path:
        return file_path.with_name(f"{file_path.stem}__assinatura_{suffix}.png")

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
