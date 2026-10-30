"""Bounded worker diagnostics, independent of operational SQLite admission.

The directory is private and shared by web/worker. A lifetime OS lock prevents
another invocation from replacing the live owner's observations. Heartbeats
prove recent activity of this process, not progress or external delivery.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import threading
import time
import uuid

from src.services.file_mutex import file_mutex

LOGGER = logging.getLogger(__name__)
HEARTBEAT_SECONDS = 10
STALE_SECONDS = 35
MAX_BYTES = 4096
PHASES = {"starting", "cycling", "idle", "waiting", "refused", "failed", "stopped"}
CODES = {"cycle_completed", "worker_busy", "contract_busy", "identity_refused",
         "contract_refused", "storage_refused", "worker_error", "interrupted"}


def storage_id(data_dir):
    # Compare installations without publishing a filesystem path.
    return hashlib.sha256(str(Path(data_dir).resolve()).encode("utf-8")).hexdigest()


def safe_directory(directory, *, protected=()):
    if not directory:
        return None
    path = Path(directory)
    if not path.is_absolute() or str(path).startswith(("//", "\\\\")):
        raise ValueError("health_configuration_invalid")
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink() or ancestor.is_junction():
            raise ValueError("health_configuration_invalid")
    path = path.resolve()
    if any(path.is_relative_to(Path(item).resolve()) for item in protected):
        raise ValueError("health_configuration_invalid")
    if path.exists() and not path.is_dir():
        raise ValueError("health_configuration_invalid")
    return path


def configured_channel(*, release_root=None):
    # Lazy config import keeps the offline reader independent of .env/app startup.
    from src import config
    root = Path(release_root) if release_root else Path(__file__).resolve().parents[2]
    directory = safe_directory(config.WORKER_HEALTH_DIR, protected=(
        root / "src", root / "tools", root / ".git", config.APP_DATA_DIR,
        config.GRAPH_CACHE_DIR, config.EXCEL_ACTIVAS_DIR,
        config.EXCEL_ARQUIVADAS_DIR, config.EXCEL_CANCELADAS_DIR))
    return directory, storage_id(config.APP_DATA_DIR)


def _number(value):
    return type(value) in (int, float) and 0 <= value <= 1e12 and math.isfinite(value)


def _hex(value, length):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{" + str(length) + "}", value) is not None


def read_health(directory, expected_storage, *, expected_sha256=None, now=None):
    """Read only fixed fields; a malformed/missing channel is never 'healthy'."""
    if directory is None:
        return {"state": "not_configured"}
    try:
        path = Path(directory) / "worker.json"
        if path.is_symlink() or path.is_junction():
            raise ValueError()
        with path.open("rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError()
        data = json.loads(raw)
        if not isinstance(data, dict) or type(data.get("format")) is not int or data["format"] != 1:
            raise ValueError()
        if not _hex(data.get("storage_id"), 64):
            raise ValueError()
        if data["storage_id"] != expected_storage:
            return {"state": "wrong_storage"}
        if data.get("phase") not in PHASES or data.get("last_result_code") not in CODES | {None}:
            raise ValueError()
        fields = ("started_at", "heartbeat_at", "cycle_started_at", "cycle_completed_at", "last_result_at")
        for field in fields:
            if not _number(data.get(field)) and not (field not in fields[:2] and data.get(field) is None):
                raise ValueError()
        release = data.get("release")
        if release is not None:
            if (not isinstance(release, dict) or not _hex(release.get("source_sha256"), 64)
                    or not (_hex(release.get("revision"), 40) or release.get("revision") == "working-tree")
                    or type(release.get("verified")) is not bool):
                raise ValueError()
            release = {key: release[key] for key in ("source_sha256", "revision", "verified")}
        current = time.time() if now is None else now
        age = current - data["heartbeat_at"]
        phase = data["phase"]
        mismatch = bool(release and expected_sha256 and release["source_sha256"] != expected_sha256)
        state = ("clock_invalid" if age < -5 else
                 phase if phase in {"refused", "failed", "stopped"} else
                 "stale" if age > STALE_SECONDS else
                 "version_mismatch" if mismatch else phase)
        return {"state": state, "phase": phase, "release": release,
                "observed_at": current,
                "version_mismatch": mismatch, "heartbeat_age_seconds": max(0, age),
                "stale_after_seconds": STALE_SECONDS,
                "last_result_code": data.get("last_result_code"),
                **{field: data.get(field) for field in fields}}
    except FileNotFoundError:
        return {"state": "never_seen"}
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        return {"state": "unavailable"}


def configured_status(release_info):
    try:
        directory, identity = configured_channel()
        return read_health(directory, identity, expected_sha256=release_info["source_sha256"])
    except (OSError, ValueError):
        return {"state": "configuration_invalid"}


class WorkerHealth:
    def __init__(self, directory, identity):
        self.directory = directory
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = None
        self.guard = None
        self.warned = False
        self.data = {"format": 1, "storage_id": identity, "phase": "starting", "release": None,
                     "started_at": time.time(), "heartbeat_at": time.time(),
                     "cycle_started_at": None, "cycle_completed_at": None,
                     "last_result_code": None, "last_result_at": None}

    def __enter__(self):
        if self.directory is None:
            return self
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            lock_path = self.directory / "writer.lock"
            if lock_path.is_symlink() or lock_path.is_junction():
                raise OSError("Unsafe diagnostic lock")
            self.guard = file_mutex(lock_path)
            self.guard.__enter__()
        except OSError:
            self.guard = None
            self.directory = None
            self._warn()
            return self
        self.update("starting")
        self.thread = threading.Thread(target=self._heartbeat, daemon=True, name="worker-health")
        self.thread.start()
        return self

    def _warn(self):
        if not self.warned:
            LOGGER.warning("worker_health_unavailable: não foi possível atualizar o diagnóstico independente.")
            self.warned = True

    def update(self, phase=None, *, code=None, release=None):
        with self.lock:
            now = time.time()
            if phase:
                self.data["phase"] = phase
                if phase == "cycling":
                    self.data["cycle_started_at"] = now
            if code:
                self.data.update(last_result_code=code, last_result_at=now)
                if code in {"cycle_completed", "worker_busy"}:
                    self.data["cycle_completed_at"] = now
            if release:
                self.data["release"] = {key: release[key] for key in ("revision", "source_sha256", "verified")}
            self.data["heartbeat_at"] = now
            if self.directory is None:
                return
            temporary = self.directory / (".worker-" + uuid.uuid4().hex + ".tmp")
            try:
                # Atomic replace leaves readers either complete observation.
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    json.dump(self.data, handle, allow_nan=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                temporary.replace(self.directory / "worker.json")
            except OSError:
                self._warn()
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _heartbeat(self):
        while not self.stop.wait(HEARTBEAT_SECONDS):
            self.update()

    def __exit__(self, exc_type, exc, traceback):
        self.stop.set()
        if self.thread:
            self.thread.join()
        if self.guard:
            self.guard.__exit__(exc_type, exc, traceback)
