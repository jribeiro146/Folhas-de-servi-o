"""Authentication storage and technician user bootstrap."""

from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from werkzeug.security import check_password_hash, generate_password_hash

from src.config import AUTH_DATABASE_PATH
from src.document_schema import TECHNICIAN_OPTIONS, get_technician_initials


@dataclass(frozen=True)
class AuthUser:
    id: int
    username: str
    initials: str
    display_name: str
    role: str


class AuthService:
    """Small SQLite-backed auth service for field technicians."""

    def __init__(self, database_path: str | Path | None = None):
        self.database_path = Path(database_path or AUTH_DATABASE_PATH)

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    initials TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'technician',
                    password_hash TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_login_at TEXT
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    action TEXT NOT NULL,
                    details TEXT,
                    ip_address TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                );
                """
            )
            self._ensure_default_users(connection)

    def authenticate(self, identifier: str, password: str, *, ip_address: str | None = None) -> AuthUser | None:
        normalized_identifier = self._normalize_identifier(identifier)
        if not normalized_identifier or not password:
            return None

        for row in self._active_user_rows():
            candidates = {
                self._normalize_identifier(row["username"]),
                self._normalize_identifier(row["initials"]),
                self._normalize_identifier(row["display_name"]),
            }
            if normalized_identifier not in candidates:
                continue

            if not check_password_hash(row["password_hash"], password):
                self.log_action(None, "login_failed", row["username"], ip_address=ip_address)
                return None

            now = self._now()
            with self._connect() as connection:
                connection.execute(
                    "UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, row["id"]),
                )
            user = self._row_to_user(row)
            self.log_action(user.id, "login", user.username, ip_address=ip_address)
            return user

        self.log_action(None, "login_failed", normalized_identifier, ip_address=ip_address)
        return None

    def get_user(self, user_id: int | None) -> AuthUser | None:
        if not user_id:
            return None

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, username, initials, display_name, role
                FROM users
                WHERE id = ? AND is_active = 1
                """,
                (user_id,),
            ).fetchone()

        return self._row_to_user(row) if row else None

    def log_action(
        self,
        user_id: int | None,
        action: str,
        details: str | None = None,
        *,
        ip_address: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_log (user_id, action, details, ip_address, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, action, details, ip_address, self._now()),
            )

    def _active_user_rows(self) -> list[sqlite3.Row]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, username, initials, display_name, role, password_hash
                FROM users
                WHERE is_active = 1
                ORDER BY display_name
                """
            ).fetchall()
        return list(rows)

    def _ensure_default_users(self, connection: sqlite3.Connection) -> None:
        now = self._now()
        for technician_name in TECHNICIAN_OPTIONS:
            initials = get_technician_initials(technician_name)
            if not initials:
                continue

            username = initials.lower()
            password = f"{initials}sensor"
            connection.execute(
                """
                INSERT OR IGNORE INTO users (
                    username,
                    initials,
                    display_name,
                    role,
                    password_hash,
                    is_active,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, 'technician', ?, 1, ?, ?)
                """,
                (
                    username,
                    initials,
                    technician_name,
                    generate_password_hash(password),
                    now,
                    now,
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.database_path))
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _row_to_user(row: sqlite3.Row | dict[str, Any]) -> AuthUser:
        return AuthUser(
            id=int(row["id"]),
            username=str(row["username"]),
            initials=str(row["initials"]),
            display_name=str(row["display_name"]),
            role=str(row["role"]),
        )

    @staticmethod
    def _normalize_identifier(value: str | None) -> str:
        return str(value or "").strip().casefold()

    @staticmethod
    def _now() -> str:
        return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()
