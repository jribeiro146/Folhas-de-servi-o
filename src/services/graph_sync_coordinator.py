"""Non-blocking refresh coordinator for the SharePoint active-file cache."""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from src.logging_config import log_event
from src.services.graph_storage_service import GraphStorageService


LOGGER = logging.getLogger(__name__)


class GraphRefreshCoordinator:
    """Refresh Graph cache in a daemon thread while requests use local data."""

    def __init__(
        self,
        graph_service: GraphStorageService,
        local_active_dir: str | Path,
        *,
        on_complete: Callable[[], None] | None = None,
        refresh_seconds: int | None = None,
    ):
        self.graph_service = graph_service
        self.local_active_dir = Path(local_active_dir)
        self.on_complete = on_complete
        configured = int(os.environ.get("FS_GRAPH_REFRESH_SECONDS", "30"))
        self.refresh_seconds = max(int(refresh_seconds or configured), 5)
        self._lock = threading.Lock()
        self._in_progress = False
        self._last_started_at: float | None = None
        self._last_completed_at: float | None = None
        self._last_error: str | None = None
        self._synced_files: list[str] = []

    def request_refresh(self, *, force: bool = False) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            if self._in_progress:
                return self._status_unlocked()
            if (
                not force
                and self._last_started_at is not None
                and now - self._last_started_at < self.refresh_seconds
            ):
                return self._status_unlocked()
            self._in_progress = True
            self._last_started_at = now
            self._last_error = None

        log_event(
            LOGGER,
            logging.INFO,
            "Atualização da cache Graph iniciada.",
            event="graph_refresh_started",
            forced=force,
        )
        threading.Thread(target=self._run, daemon=True, name="graph-active-refresh").start()
        return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._status_unlocked()

    def _status_unlocked(self) -> dict[str, Any]:
        return {
            "in_progress": self._in_progress,
            "last_started_at": self._last_started_at,
            "last_completed_at": self._last_completed_at,
            "last_error": self._last_error,
            "synced_files": list(self._synced_files),
        }

    def _run(self) -> None:
        started_at = time.perf_counter()
        error: str | None = None
        synced_files: list[str] = []
        try:
            files = self.graph_service.sync_active_files(self.local_active_dir)
            synced_files = [path.name for path in files]
            if self.on_complete is not None:
                self.on_complete()
        except Exception as exc:  # the status endpoint exposes a safe summary
            error = str(exc)
            log_event(
                LOGGER,
                logging.ERROR,
                "Atualização da cache Graph falhou.",
                event="graph_refresh_failed",
                duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
                exc_info=True,
            )
        else:
            log_event(
                LOGGER,
                logging.INFO,
                "Atualização da cache Graph concluída.",
                event="graph_refresh_completed",
                synced_file_count=len(synced_files),
                duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
            )
        finally:
            with self._lock:
                self._in_progress = False
                self._last_completed_at = time.time()
                self._last_error = error
                self._synced_files = synced_files
