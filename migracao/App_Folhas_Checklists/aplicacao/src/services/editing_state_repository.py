"""Transactional SQLite persistence for document editing state."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator


class SqliteStateRepository:
    """Store editing aggregates inside short cross-process transactions."""

    def __init__(self, database_path: str | Path, *, legacy_root: str | Path | None = None):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.busy_timeout_ms = max(int(os.environ.get("FS_STATE_DB_BUSY_MS", "10000")), 1000)
        self._initialize()
        if legacy_root is not None:
            self._import_legacy_states(Path(legacy_root))

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        deadline = time.monotonic() + (self.busy_timeout_ms / 1000)
        while True:
            try:
                with self._connect() as connection:
                    connection.execute("PRAGMA journal_mode = WAL")
                    connection.execute("PRAGMA synchronous = NORMAL")
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS editing_states (
                            document_id TEXT PRIMARY KEY,
                            state_json TEXT NOT NULL,
                            updated_at REAL NOT NULL
                        )
                        """
                    )
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).casefold() or time.monotonic() >= deadline:
                    raise
                time.sleep(0.025)

    @contextmanager
    def transaction(
        self,
        document_id: str,
        factory: Callable[[], dict[str, Any]],
    ) -> Iterator[dict[str, Any]]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state_json FROM editing_states WHERE document_id = ?",
                (document_id,),
            ).fetchone()
            state = self._decode(row["state_json"]) if row else factory()
            yield state
            connection.execute(
                """
                INSERT INTO editing_states (document_id, state_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (
                    document_id,
                    json.dumps(state, ensure_ascii=False, separators=(",", ":")),
                    time.time(),
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def document_ids(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT document_id FROM editing_states ORDER BY document_id"
            ).fetchall()
        return [str(row["document_id"]) for row in rows]

    def _import_legacy_states(self, legacy_root: Path) -> None:
        """Import old JSON aggregates once without deleting recovery files."""
        candidates: list[tuple[str, str, float]] = []
        for path in legacy_root.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            document_id = str(payload.get("document_id") or "").strip()
            if not document_id:
                continue
            candidates.append(
                (
                    document_id,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    path.stat().st_mtime,
                )
            )

        if not candidates:
            return
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany(
                """
                INSERT OR IGNORE INTO editing_states (document_id, state_json, updated_at)
                VALUES (?, ?, ?)
                """,
                candidates,
            )
            connection.commit()

    @staticmethod
    def _decode(value: str) -> dict[str, Any]:
        try:
            payload = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}
