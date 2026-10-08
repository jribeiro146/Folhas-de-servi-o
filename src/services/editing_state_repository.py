"""Transactional SQLite persistence for document editing state."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator
from src.services.sqlite_storage_format import initialize_store, validate_store, StorageFormatError


class SqliteStateRepository:
    """Store editing aggregates inside short cross-process transactions."""

    def __init__(self, database_path: str | Path, *, legacy_root: str | Path | None = None):
        self.database_path = Path(database_path)
        legacy_json = legacy_root is not None and any(Path(legacy_root).glob("*.json"))
        if legacy_json and not self.database_path.exists():
            raise StorageFormatError("Estados JSON antigos requerem preparação explícita.")
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.busy_timeout_ms = max(int(os.environ.get("FS_STATE_DB_BUSY_MS", "10000")), 1000)
        self._initialize(allow_new=not legacy_json)

    def _connect(self, *, check_format=True) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys = ON")
        if check_format:
            try:
                validate_store(connection, "editing")
            except BaseException:
                connection.close()
                raise
        return connection

    def _initialize(self, *, allow_new=True) -> None:
        deadline = time.monotonic() + (self.busy_timeout_ms / 1000)
        while True:
            try:
                with self._connect(check_format=False) as connection:
                    initialize_store(connection, "editing", allow_new=allow_new)
                    connection.execute("PRAGMA journal_mode = WAL")
                    connection.execute("PRAGMA synchronous = NORMAL")
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
        *, audit: dict[str, Any] | None = None, must_exist: bool = False,
    ) -> Iterator[dict[str, Any]]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state_json FROM editing_states WHERE document_id = ?",
                (document_id,),
            ).fetchone()
            if row is None and must_exist:
                raise KeyError(document_id)
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
            if audit is not None:
                self._insert_audit(connection, audit)
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

    @staticmethod
    def _insert_audit(connection, audit):
        connection.execute(
            "INSERT INTO admin_actions(actor_email,action,target,reason,details_json,created_at) VALUES(?,?,?,?,?,?)",
            (audit["actor_email"], audit["action"], audit["target"], audit["reason"],
             json.dumps(audit.get("details") or {}, ensure_ascii=False), time.time()),
        )

    def record_admin_action(self, audit):
        with self._connect() as connection:
            self._insert_audit(connection, audit)

    def admin_history(self, limit=100):
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM admin_actions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "details": json.loads(row["details_json"])} for row in rows]

    @staticmethod
    def _decode(value: str) -> dict[str, Any]:
        try:
            payload = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}
