"""Resolve números de obra para pastas existentes no SharePoint."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlsplit

from src.document_schema import normalize_work_number
from src.services.graph_storage_service import GraphStorageService


WORK_FOLDER_NAME_RE = re.compile(r"^([0-9]{4})\s*-\s*")


class WorkFolderError(Exception):
    """Erro de resolução de uma pasta de obra."""


class WorkFolderInvalidNumberError(WorkFolderError):
    """O número de obra não é válido."""


class WorkFolderNotFoundError(WorkFolderError):
    """Não existe uma pasta com o número pedido."""


class WorkFolderAmbiguousError(WorkFolderError):
    """Mais de uma pasta corresponde ao mesmo número."""


class WorkFolderUnsafeUrlError(WorkFolderError):
    """O URL devolvido pelo SharePoint não é um destino permitido."""


@dataclass(frozen=True)
class WorkFolder:
    number: str
    name: str
    web_url: str


class WorkFolderService:
    """Mantém uma cache curta dos filhos diretos da pasta de obras."""

    def __init__(
        self,
        graph_service: GraphStorageService,
        *,
        folder_path: str,
        allowed_hostname: str,
        cache_seconds: int = 300,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.graph_service = graph_service
        self.folder_path = str(folder_path or "").strip().strip("/")
        self.allowed_hostname = str(allowed_hostname or "").strip().casefold()
        self.cache_seconds = max(int(cache_seconds), 0)
        self._clock = clock
        self._cache: dict[str, tuple[WorkFolder, ...]] = {}
        self._cache_expires_at = 0.0
        self._refresh_lock = threading.Lock()

    def resolve(self, work_number: Any) -> WorkFolder:
        number = normalize_work_number(work_number)
        if not number:
            raise WorkFolderInvalidNumberError("Número de obra inválido.")

        with self._refresh_lock:
            refreshed = False
            if self._clock() >= self._cache_expires_at:
                self._refresh_locked()
                refreshed = True

            matches = self._cache.get(number, ())
            if not matches and not refreshed:
                # A pasta pode ter sido criada depois de esta cache ser preenchida.
                self._refresh_locked()
                matches = self._cache.get(number, ())

            if not matches:
                raise WorkFolderNotFoundError(
                    f"Não foi encontrada uma pasta para a obra {number}."
                )
            if len(matches) > 1:
                raise WorkFolderAmbiguousError(
                    f"Foram encontradas {len(matches)} pastas para a obra {number}."
                )

            folder = matches[0]
            self._validate_web_url(folder.web_url)
            return folder

    def _refresh_locked(self) -> None:
        children = self.graph_service.list_folder_children(self.folder_path)
        refreshed: dict[str, list[WorkFolder]] = {}

        for item in children:
            if not isinstance(item, dict) or not isinstance(item.get("folder"), dict):
                continue

            name = str(item.get("name") or "").strip()
            match = WORK_FOLDER_NAME_RE.match(name)
            if not match:
                continue

            number = match.group(1)
            refreshed.setdefault(number, []).append(
                WorkFolder(
                    number=number,
                    name=name,
                    web_url=str(item.get("webUrl") or "").strip(),
                )
            )

        self._cache = {
            number: tuple(matches)
            for number, matches in refreshed.items()
        }
        self._cache_expires_at = self._clock() + self.cache_seconds

    def _validate_web_url(self, web_url: str) -> None:
        try:
            destination = urlsplit(web_url)
            port = destination.port
        except ValueError as exc:
            raise WorkFolderUnsafeUrlError("O SharePoint devolveu um URL inválido.") from exc

        if (
            destination.scheme.casefold() != "https"
            or destination.hostname is None
            or destination.hostname.casefold() != self.allowed_hostname
            or destination.username is not None
            or destination.password is not None
            or port not in {None, 443}
        ):
            raise WorkFolderUnsafeUrlError(
                "O SharePoint devolveu um destino fora do domínio permitido."
            )
