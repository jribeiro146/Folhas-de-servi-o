"""Non-blocking Graph refresh with one durable result across web processes."""

from __future__ import annotations

import json
import logging
import math
import os
import secrets
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from src.logging_config import log_event
from src.services.active_file_index import read_active_inventory
from src.services.file_diagnostics import SYNC_ID
from src.services.file_mutex import FileMutexBusy, file_mutex
from src.services.graph_storage_service import GraphStorageService


LOGGER = logging.getLogger(__name__)
REFRESH_STATE_NAME = ".graph_refresh_state.json"


class GraphRefreshCoordinator:
    """Keep the active run lock until its matching completion is persisted.

    The short state lock makes requesting, observing and completing a refresh
    atomic across processes. OS locks are released after a worker crash, so a
    later observer can explicitly report interruption and retry safely.
    """

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
        self._state_path = self.local_active_dir / REFRESH_STATE_NAME
        self._state_lock_path = self.local_active_dir / ".graph_refresh_state.lock"
        self._run_lock_path = self.local_active_dir / ".graph_refresh_run.lock"

    @contextmanager
    def _state_guard(self):
        deadline = time.monotonic() + 2
        while True:
            guard = file_mutex(self._state_lock_path)
            try:
                guard.__enter__()
                break
            except FileMutexBusy:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
        try:
            yield
        finally:
            guard.__exit__(None, None, None)

    def request_refresh(self, *, force: bool = False) -> dict[str, Any]:
        with self._state_guard():
            state = self._read_state()
            run_guard = file_mutex(self._run_lock_path)
            try:
                run_guard.__enter__()
            except FileMutexBusy:
                state.update(in_progress=True, outcome="running")
                return self._public_status(state)
            accepted = False
            try:
                self._recover_interrupted(state)
                now = time.time()
                if (not force and state["last_started_at"] is not None
                        and now - state["last_started_at"] < self.refresh_seconds):
                    return self._public_status(state)
                refresh_id = secrets.token_hex(16)
                if state["generation_id"] is None:
                    state.update(generation_id=secrets.token_hex(16), refresh_sequence=0,
                                 last_completed_sequence=0)
                state.update(in_progress=True, refresh_id=refresh_id, outcome="running",
                             refresh_sequence=state["refresh_sequence"] + 1,
                             last_started_at=now, last_error=None, synced_files=[])
                self._write_state(state)
                worker = threading.Thread(
                    target=self._run, args=(refresh_id, run_guard),
                    daemon=True, name="graph-active-refresh",
                )
                worker.start()
                accepted = True
                log_event(LOGGER, logging.INFO, "Atualização da cache Graph iniciada.",
                          event="graph_refresh_started", forced=force, refresh_id=refresh_id)
                return self._public_status(state)
            finally:
                # Once started, the worker owns the lock until durable completion.
                if not accepted:
                    run_guard.__exit__(None, None, None)

    def status(self) -> dict[str, Any]:
        with self._state_guard():
            state = self._read_state()
            try:
                with file_mutex(self._run_lock_path):
                    self._recover_interrupted(state)
            except FileMutexBusy:
                state.update(in_progress=True, outcome="running")
            return self._public_status(state)

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {"version": 1, "in_progress": False, "refresh_id": None, "outcome": "idle",
                "generation_id": None, "refresh_sequence": 0, "last_completed_sequence": 0,
                "last_started_at": None, "last_completed_at": None, "last_success_at": None,
                "last_completed_refresh_id": None, "last_completed_outcome": None,
                "last_error": None, "synced_files": []}

    def _read_state(self) -> dict[str, Any]:
        state = self._empty_state()
        try:
            saved = json.loads(self._state_path.read_text(encoding="utf-8"))
            if (not isinstance(saved, dict) or saved.get("version") != 1
                    or saved.get("outcome") not in {"idle", "running", "success", "error"}
                    or not isinstance(saved.get("in_progress"), bool)
                    or saved["in_progress"] != (saved["outcome"] == "running")):
                raise ValueError("invalid refresh state")
            for key in ("last_started_at", "last_completed_at", "last_success_at"):
                if saved.get(key) is not None and (
                    isinstance(saved[key], bool) or not isinstance(saved[key], (int, float))
                    or not math.isfinite(saved[key]) or saved[key] <= 0
                ):
                    raise ValueError("invalid refresh timestamp")
            for key in ("refresh_id", "last_completed_refresh_id", "last_error"):
                if saved.get(key) is not None and not isinstance(saved[key], str):
                    raise ValueError("invalid refresh result")
            if saved["outcome"] in {"running", "success"} and not saved.get("refresh_id"):
                raise ValueError("missing refresh identifier")
            if saved.get("last_completed_outcome") not in {None, "success", "error"}:
                raise ValueError("invalid completion outcome")
            if not isinstance(saved.get("synced_files"), list) or not all(
                isinstance(name, str) for name in saved["synced_files"]
            ):
                raise ValueError("invalid synchronized files")
            generation = saved.get("generation_id")
            sequence = saved.get("refresh_sequence", 0)
            completed_sequence = saved.get("last_completed_sequence", 0)
            if (generation is not None and (not isinstance(generation, str) or not generation)
                    or isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0
                    or isinstance(completed_sequence, bool) or not isinstance(completed_sequence, int)
                    or not 0 <= completed_sequence <= sequence):
                raise ValueError("invalid refresh sequence")
            # Legacy snapshots remain readable, but cannot prove completion of
            # a numbered request until the first new request persists a generation.
            if (generation is None and (sequence or completed_sequence)
                    or generation is not None and sequence == 0):
                raise ValueError("invalid refresh generation")
            state.update({key: saved[key] for key in state if key in saved})
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError):
            state.update(outcome="error", last_error="Não foi possível ler o estado da atualização.")
        return state

    def _write_state(self, state: dict[str, Any]) -> None:
        GraphStorageService._atomic_write_json(self._state_path, state)

    def _recover_interrupted(self, state: dict[str, Any]) -> None:
        # Caller holds both state and run locks; no matching worker can remain.
        if state["in_progress"]:
            state.update(in_progress=False, outcome="error", last_completed_at=time.time(),
                         last_completed_refresh_id=state["refresh_id"], last_completed_outcome="error",
                         last_completed_sequence=state["refresh_sequence"],
                         last_error="A atualização foi interrompida. Volte a atualizar a lista.")
            self._write_state(state)

    def _public_status(self, state: dict[str, Any]) -> dict[str, Any]:
        result = {key: value for key, value in state.items() if key != "version"}
        inventory = read_active_inventory(self.local_active_dir)
        result["inventory"] = {
            "available": inventory["available"], "updated_at": inventory["updated_at"],
            "refresh_id": inventory["refresh_id"], "error": inventory["error"],
            "file_count": len(inventory["files"]),
            "unavailable_count": len(inventory["unavailable_files"]),
            "unavailable_files": sorted(inventory["unavailable_files"]),
            "stale": (not inventory["available"] or inventory["updated_at"] is None
                      or state["outcome"] == "error"
                      or time.time() - inventory["updated_at"] > self.refresh_seconds),
        }
        return result

    def _run(self, refresh_id: str, run_guard) -> None:
        started_at = time.perf_counter()
        error: str | None = None
        synced_files: list[str] = []
        context = SYNC_ID.set(refresh_id)
        try:
            files = self.graph_service.sync_active_files(self.local_active_dir)
            synced_files = [path.name for path in files]
            if self.on_complete is not None:
                self.on_complete()
        except Exception:
            # Do not expose exception text containing paths, signed URLs or
            # transport details through the public status endpoint.
            error = "Não foi possível concluir a atualização. A lista pode estar desatualizada ou ter conteúdo em falta."
            log_event(LOGGER, logging.ERROR, "Atualização da cache Graph falhou.",
                      event="graph_refresh_failed", refresh_id=refresh_id,
                      duration_ms=round((time.perf_counter() - started_at) * 1000, 2), exc_info=True)
        else:
            log_event(LOGGER, logging.INFO, "Atualização da cache Graph concluída.",
                      event="graph_refresh_completed", refresh_id=refresh_id,
                      synced_file_count=len(synced_files),
                      duration_ms=round((time.perf_counter() - started_at) * 1000, 2))
        finally:
            SYNC_ID.reset(context)
            try:
                with self._state_guard():
                    state = self._read_state()
                    now = time.time()
                    if state["refresh_id"] == refresh_id:
                        outcome = "error" if error else "success"
                        state.update(in_progress=False, outcome=outcome, last_completed_at=now,
                                     last_completed_refresh_id=refresh_id, last_completed_outcome=outcome,
                                     last_completed_sequence=state["refresh_sequence"],
                                     last_error=error, synced_files=synced_files)
                        if error is None:
                            state["last_success_at"] = now
                        self._write_state(state)
                    run_guard.__exit__(None, None, None)
                    run_guard = None
            finally:
                if run_guard is not None:
                    run_guard.__exit__(None, None, None)
