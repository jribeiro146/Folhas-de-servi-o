"""Release admission on one local disk; no app configuration or transports.

Each admitted operation holds a shared SQLite rollback-journal transaction.
Changing the active release requires an exclusive transaction on this database.
The application databases retain their own transactions and WAL configuration.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
import uuid

from src.services.file_mutex import file_mutex, FileMutexBusy


CONTRACT_FILE = "runtime-contract.sqlite3"
APPLICATION_ID = 0x46534354
CONTRACT_VERSION = 1
# Versions of the storage contracts, not individual document schema versions.
SUPPORTED_FORMATS = {"editing": 1, "queue": 1, "bundle": 1, "sadi": 1, "inventory": 1}


class ContractError(RuntimeError):
    code = "state_contract_invalid"


class ContractBusy(ContractError):
    code = "state_contract_busy"


def _validate_source(source: str) -> None:
    if not isinstance(source, str) or re.fullmatch(r"[0-9a-f]{64}", source) is None:
        raise ValueError("Identificador SHA-256 de código inválido.")


class RuntimeContract:
    def __init__(self, data_dir: Path, source_sha256: str):
        _validate_source(source_sha256)
        self.data_dir = Path(data_dir).resolve()
        self.path = self.data_dir / CONTRACT_FILE
        self.source_sha256 = source_sha256

    def initialize_empty(self) -> dict:
        """Explicitly initialize NEW storage; never adopt existing data here."""
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = self.data_dir / ".runtime-contract.init.lock"
        try:
            with file_mutex(lock):
                if self.path.exists():
                    with self.operation() as snapshot:
                        return snapshot
                if any(path != lock for path in self.data_dir.iterdir()):
                    raise ContractError("Os dados existentes requerem preparação e migração explícitas; não são uma instalação vazia.")
                temporary = self.data_dir / (".runtime-contract-" + uuid.uuid4().hex + ".tmp")
                try:
                    with temporary.open("xb"):
                        pass
                    connection = sqlite3.connect(temporary, isolation_level=None)
                    try:
                        connection.execute("PRAGMA journal_mode=DELETE")
                        connection.execute("PRAGMA synchronous=FULL")
                        connection.execute("BEGIN EXCLUSIVE")
                        connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
                        connection.execute(f"PRAGMA user_version={CONTRACT_VERSION}")
                        connection.execute("""CREATE TABLE state_contract (
                            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                            source_sha256 TEXT NOT NULL, formats_json TEXT NOT NULL,
                            epoch INTEGER NOT NULL)""")
                        connection.execute("INSERT INTO state_contract VALUES(1,?,?,1)",
                                           (self.source_sha256, json.dumps(SUPPORTED_FORMATS, sort_keys=True)))
                        connection.commit()
                    finally:
                        connection.close()
                    temporary.replace(self.path)
                finally:
                    temporary.unlink(missing_ok=True)
        except FileMutexBusy as exc:
            raise ContractBusy("A preparação dos dados já está em curso.") from exc
        return self.inspect()

    def _read(self, connection) -> dict:
        if (connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete"
                or connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                or connection.execute("PRAGMA user_version").fetchone()[0] != CONTRACT_VERSION):
            raise ContractError("Formato ou modo do contrato de dados incompatível.")
        rows = connection.execute("SELECT singleton, source_sha256, formats_json, epoch FROM state_contract").fetchmany(2)
        if len(rows) != 1 or rows[0][0] != 1:
            raise ContractError("Contrato de dados inválido; requer diagnóstico.")
        _, source, raw_formats, epoch = rows[0]
        try:
            _validate_source(source)
            formats = json.loads(raw_formats)
            if (not isinstance(formats, dict) or formats != SUPPORTED_FORMATS
                    or any(type(value) is not int for value in formats.values())
                    or type(epoch) is not int or epoch < 1):
                raise ValueError("Invalid state formats")
        except (ValueError, TypeError) as exc:
            raise ContractError("Formatos persistentes incompatíveis; requer migração explícita.") from exc
        return {"source_sha256": source, "formats": formats, "epoch": epoch,
                "matches_release": source == self.source_sha256}

    @contextmanager
    def _session(self, *, exclusive=False, require_match=True):
        connection = None
        try:
            # mode=rw deliberately refuses a missing database instead of creating one.
            connection = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True,
                                         isolation_level=None, timeout=0.25)
            connection.execute("BEGIN EXCLUSIVE" if exclusive else "BEGIN")
            snapshot = self._read(connection)
            if require_match and not snapshot["matches_release"]:
                raise ContractError("Versão de código incompatível com os dados ativos; aguarde atualização coordenada.")
        except sqlite3.Error as exc:
            if connection is not None:
                connection.close()
            if (getattr(exc, "sqlite_errorcode", 0) & 255) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                raise ContractBusy("Há operações ou uma atualização em curso; volte a tentar.") from exc
            raise ContractError("Contrato de dados indisponível ou inválido; confirmar preparação e armazenamento.") from exc
        except ContractError:
            if connection is not None:
                connection.close()
            raise
        try:
            yield connection, snapshot
        finally:
            connection.rollback()
            connection.close()

    @contextmanager
    def operation(self):
        """Hold admission until the caller's ENTIRE operation has finished."""
        with self._session() as (_, snapshot):
            yield snapshot

    def inspect(self) -> dict:
        """Read identifiers only, even when this process has a different release."""
        with self._session(require_match=False) as (_, snapshot):
            return snapshot

    def activate(self, next_source_sha256: str) -> dict:
        """Switch compatible code after all admitted operations have stopped.

        The instance's source is the expected previous release. This is not a
        migration or rollback command and never changes storage format markers.
        """
        _validate_source(next_source_sha256)
        with self._session(exclusive=True) as (connection, snapshot):
            if snapshot["source_sha256"] != next_source_sha256:
                connection.execute("UPDATE state_contract SET source_sha256=?, epoch=epoch+1 WHERE singleton=1",
                                   (next_source_sha256,))
            result = {**self._read(connection), "matches_release": True}
            connection.commit()
            # Return this committed transition, without a racy second connection.
            return result
