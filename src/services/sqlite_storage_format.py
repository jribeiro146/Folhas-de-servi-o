"""Explicit identities for operational SQLite stores; no implicit legacy upgrade."""
from contextlib import closing
from functools import lru_cache
from pathlib import Path
import re
import sqlite3

from src.services.runtime_contract import ContractError, ContractBusy


class StorageFormatError(ContractError):
    code = "storage_format_invalid"


IDENTITIES = {"editing": 0x46534544, "queue": 0x46535155}
VERSION = 1
SCHEMAS = {
    "editing": (
        """CREATE TABLE editing_states (
            document_id TEXT PRIMARY KEY, state_json TEXT NOT NULL, updated_at REAL NOT NULL)""",
        """CREATE TABLE admin_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, actor_email TEXT NOT NULL,
            action TEXT NOT NULL, target TEXT NOT NULL, reason TEXT NOT NULL,
            details_json TEXT NOT NULL, created_at REAL NOT NULL)""",
    ),
    "queue": (
        """CREATE TABLE graph_sync_jobs (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload_json TEXT NOT NULL,
            status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at REAL NOT NULL, retryable INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER, last_error TEXT, result_json TEXT,
            created_at REAL NOT NULL, updated_at REAL NOT NULL,
            attempt_token TEXT NOT NULL DEFAULT '')""",
    ),
}


def _normalized_ddl(sql):
    # Preserve quoted literals verbatim; normalize only unquoted layout/case.
    # Other spellings need explicit preparation, not a permissive SQL parser.
    tokens = re.findall(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|[^\s'\"]+", sql or "")
    return "".join(token if token.startswith(("'", '"')) else token.casefold() for token in tokens)


def _shape(connection):
    objects = connection.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE type IN ('table','view','trigger') "
        "AND substr(name,1,7) != 'sqlite_' ORDER BY name"
    ).fetchall()
    result = {}
    for object_type, name, sql in objects:
        if object_type != "table" or name not in {"editing_states", "admin_actions", "graph_sync_jobs"}:
            raise StorageFormatError("Estrutura SQLite desconhecida; requer preparação explícita.")
        # Names above are a fixed allowlist, never user SQL. Include defaults,
        # nullability, primary/foreign keys and generated/hidden columns.
        columns = tuple(sorted(tuple(row)[1:] for row in connection.execute(f"PRAGMA table_xinfo({name})")))
        foreign_keys = tuple(tuple(row) for row in connection.execute(f"PRAGMA foreign_key_list({name})"))
        unique_indexes = tuple(sorted((row[2], row[3], row[4]) for row in
                                     connection.execute(f"PRAGMA index_list({name})") if row[2]))
        result[name] = (columns, foreign_keys, unique_indexes, _normalized_ddl(sql))
    return result


@lru_cache(maxsize=2)
def _expected_shape(kind):
    with closing(sqlite3.connect(":memory:")) as connection:
        for statement in SCHEMAS[kind]:
            connection.execute(statement)
        return _shape(connection)


def validate_store(connection, kind, *, allow_empty=False):
    """Return False only for a genuinely empty, unmarked database during init."""
    try:
        identity = connection.execute("PRAGMA application_id").fetchone()[0]
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        shape = _shape(connection)
        if allow_empty and identity == 0 and version == 0 and not shape:
            return False
        if identity != IDENTITIES[kind] or version != VERSION or shape != _expected_shape(kind):
            raise StorageFormatError(f"Formato SQLite de {kind} incompatível ou não preparado; requer migração explícita.")
        return True
    except sqlite3.Error as exc:
        if (getattr(exc, "sqlite_errorcode", 0) & 255) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            raise
        raise StorageFormatError(f"Base SQLite de {kind} ilegível; requer diagnóstico.") from exc


def initialize_store(connection, kind, *, allow_new=True):
    """Atomically create only empty stores; preserve legacy/unknown databases."""
    try:
        connection.execute("BEGIN IMMEDIATE")
        if not validate_store(connection, kind, allow_empty=allow_new):
            for statement in SCHEMAS[kind]:
                connection.execute(statement)
            connection.execute(f"PRAGMA application_id={IDENTITIES[kind]}")
            connection.execute(f"PRAGMA user_version={VERSION}")
            validate_store(connection, kind)
        connection.commit()
    except sqlite3.Error as exc:
        connection.rollback()
        if (getattr(exc, "sqlite_errorcode", 0) & 255) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            raise
        raise StorageFormatError(f"Base SQLite de {kind} ilegível; requer diagnóstico.") from exc
    except BaseException:
        connection.rollback()
        raise


def preflight_stores(data_dir):
    """Inspect every configured store before factories create other services."""
    root = Path(data_dir)
    for kind, path in (("editing", root / "editing-state" / "editing-state.sqlite3"),
                       ("queue", root / "graph-sync.sqlite3")):
        legacy_json = kind == "editing" and any(path.parent.glob("*.json"))
        if path.exists():
            try:
                # rw never creates a missing file, but permits SQLite to recover
                # its hot journal after a crash before inspecting the schema.
                with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True, timeout=1)) as db:
                    # Read all metadata from one snapshot while a concurrent
                    # initializer may publish its schema/markers atomically.
                    db.execute("BEGIN")
                    validate_store(db, kind, allow_empty=not legacy_json)
            except sqlite3.Error as exc:
                if (getattr(exc, "sqlite_errorcode", 0) & 255) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                    raise ContractBusy(f"Base SQLite de {kind} temporariamente ocupada.") from exc
                raise StorageFormatError(f"Não foi possível verificar a base SQLite de {kind}.") from exc
        elif legacy_json:
            raise StorageFormatError("Estados JSON antigos requerem preparação explícita antes do arranque.")
