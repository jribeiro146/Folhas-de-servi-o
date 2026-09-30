"""Persistent transactional outbox for Microsoft Graph operations."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from src.logging_config import log_event
from src.services.graph_mail_service import GraphMailService
from src.services.graph_storage_service import GraphStorageService
from src.services.local_pdf_service import LocalPdfService
from src.services.teams_notification_service import TeamsNotificationService
from src.services.local_changes import DIRTY_MARKER, bundle_guard, dirty_version, mark_dirty


LOGGER = logging.getLogger(__name__)


class GraphJobNoLongerActiveError(RuntimeError):
    """Impede que um worker órfão continue até um envio externo."""

    retryable = False


class GraphSyncQueue:
    """Persist Graph work before executing it and retry transient failures."""

    def __init__(
        self,
        graph_service: GraphStorageService | None,
        database_path: str | Path,
        *,
        mail_service: GraphMailService | None = None,
        local_pdf_service: LocalPdfService | None = None,
        teams_notification_service: TeamsNotificationService | None = None,
        auto_start: bool = True,
    ):
        self.graph_service = graph_service
        self.mail_service = mail_service
        self.local_pdf_service = local_pdf_service
        self.teams_notification_service = teams_notification_service
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.asset_root = self.database_path.parent / "graph-sync-assets"
        self.asset_root.mkdir(parents=True, exist_ok=True)
        self.busy_timeout_ms = max(int(os.environ.get("FS_STATE_DB_BUSY_MS", "10000")), 1000)
        self.mail_job_max_attempts = max(
            int(os.environ.get("FS_MAIL_JOB_MAX_ATTEMPTS", "3")),
            1,
        )
        self.mail_job_stale_seconds = max(
            int(os.environ.get("FS_MAIL_JOB_STALE_SECONDS", "900")),
            90,
        )
        self.retry_base_seconds = max(
            float(os.environ.get("FS_GRAPH_JOB_RETRY_BASE_SECONDS", "2")),
            0.01,
        )
        self.commit_guard_seconds = max(
            int(os.environ.get("FS_GRAPH_COMMIT_GUARD_SECONDS", "3600")), 60
        )
        self.auto_start = bool(auto_start)
        self._worker_lock = threading.Lock()
        self._worker_running = False
        self._initialize()
        self._start_if_enabled()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
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
                        CREATE TABLE IF NOT EXISTS graph_sync_jobs (
                            id TEXT PRIMARY KEY,
                            kind TEXT NOT NULL,
                            payload_json TEXT NOT NULL,
                            status TEXT NOT NULL,
                            attempts INTEGER NOT NULL DEFAULT 0,
                            next_attempt_at REAL NOT NULL,
                            retryable INTEGER NOT NULL DEFAULT 0,
                            max_attempts INTEGER,
                            last_error TEXT,
                            result_json TEXT,
                            created_at REAL NOT NULL,
                            updated_at REAL NOT NULL
                        )
                        """
                    )
                    columns = {
                        str(row[1])
                        for row in connection.execute("PRAGMA table_info(graph_sync_jobs)")
                    }
                    legacy_queue = "retryable" not in columns
                    if legacy_queue:
                        connection.execute(
                            "ALTER TABLE graph_sync_jobs "
                            "ADD COLUMN retryable INTEGER NOT NULL DEFAULT 0"
                        )
                    if "max_attempts" not in columns:
                        connection.execute(
                            "ALTER TABLE graph_sync_jobs ADD COLUMN max_attempts INTEGER"
                        )
                    if legacy_queue:
                        self._hold_legacy_mail_jobs(connection)
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).casefold() or time.monotonic() >= deadline:
                    raise
                time.sleep(0.025)

    def _hold_legacy_mail_jobs(self, connection: sqlite3.Connection) -> None:
        hold_message = (
            "Envio criado por uma versão anterior; requer repetição manual antes de continuar."
        )
        rows = connection.execute(
            """
            SELECT id, kind, payload_json, status, last_error
            FROM graph_sync_jobs
            WHERE status IN ('pending', 'running', 'failed')
            """
        ).fetchall()
        now = time.time()
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError):
                payload = {}
            has_mail = row["kind"] == "archive_and_mail_local" or bool(payload.get("mail"))
            if not has_mail:
                continue
            previous_error = str(row["last_error"] or "").strip()
            visible_error = (
                f"{previous_error} {hold_message}" if previous_error else hold_message
            )
            cursor = connection.execute(
                """
                UPDATE graph_sync_jobs
                SET status = 'failed', retryable = 0, max_attempts = ?,
                    next_attempt_at = ?, last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    self.mail_job_max_attempts,
                    now,
                    visible_error[:2000],
                    now,
                    row["id"],
                ),
            )
            if cursor.rowcount:
                log_event(
                    LOGGER,
                    logging.WARNING,
                    "Trabalho de uma versão anterior retido para revisão manual.",
                    event="graph_job_legacy_held",
                    job_id=row["id"],
                    job_kind=row["kind"],
                )

    def enqueue(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        identifier = job_id or str(uuid.uuid4())
        now = time.time()
        max_attempts = self._job_max_attempts(kind, payload)
        created = False
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM graph_sync_jobs WHERE id = ?", (identifier,)
            ).fetchone()
            if existing is None:
                created = True
                prepared_payload = self._stage_payload(identifier, kind, payload)
                connection.execute(
                    """
                    INSERT INTO graph_sync_jobs (
                        id, kind, payload_json, status, attempts,
                        next_attempt_at, max_attempts, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?)
                    """,
                    (
                        identifier,
                        kind,
                        json.dumps(
                            prepared_payload,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        "held" if prepared_payload.get("_commit_guard") else "pending",
                        now,
                        max_attempts,
                        now,
                        now,
                    ),
                )
            connection.commit()
        if created:
            log_event(
                LOGGER,
                logging.INFO,
                "Trabalho colocado na fila persistente.",
                event="graph_job_enqueued",
                job_id=identifier,
                job_kind=kind,
                max_attempts=max_attempts,
            )
        self._start_if_enabled()
        return self.status(identifier) or {"id": identifier, "status": "pending"}

    def _start_if_enabled(self) -> None:
        if self.auto_start:
            self.start()

    def start(self) -> bool:
        """Arranca o worker interno, quando a aplicacao gere a propria fila."""
        with self._worker_lock:
            if self._worker_running:
                return False
            self._worker_running = True
        threading.Thread(target=self._worker, daemon=True, name="graph-sync-outbox").start()
        return True

    def run_until_idle(self) -> bool:
        """Processa sincronamente todos os trabalhos prontos e termina.

        Este modo destina-se a workers externos (por exemplo, uma tarefa agendada
        no Plesk), para que o trabalho nao dependa da vida do processo WSGI.
        """
        with self._worker_lock:
            if self._worker_running:
                return False
            self._worker_running = True
        self._worker(schedule_follow_up=False)
        return True

    def _job_max_attempts(self, kind: str, payload: dict[str, Any]) -> int | None:
        has_mail = kind == "archive_and_mail_local" or bool(payload.get("mail"))
        return self.mail_job_max_attempts if has_mail else None

    def status(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            self._hold_stale_mail_jobs(connection, now=time.time())
            row = connection.execute(
                "SELECT * FROM graph_sync_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._serialize(row) if row else None

    def retry(self, job_id: str) -> dict[str, Any]:
        """Repete explicitamente um único trabalho que ficou terminalmente falhado."""
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._retire_superseded_uploads(connection)
            self._hold_outdated_upload_retries(connection)
            row = connection.execute(
                "SELECT * FROM graph_sync_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise KeyError(job_id)
            current = self._serialize(row)
            if (current["result"] or {}).get("blocked_by_newer_upload"):
                connection.commit()
                raise ValueError("Existe uma gravação posterior. Repita a publicação mais recente.")
            if current["status"] != "failed" or current["will_retry"]:
                connection.rollback()
                raise ValueError("Apenas trabalhos falhados e parados podem ser repetidos.")
            connection.execute(
                """
                UPDATE graph_sync_jobs
                SET status = ?, attempts = 0, next_attempt_at = ?,
                    retryable = 0, last_error = NULL, updated_at = ?
                WHERE id = ?
                """,
                ("held" if (current["payload"].get("_commit_guard")
                            or (current["result"] or {}).get("commit_guard_required")) else "pending",
                 now, now, job_id),
            )
            connection.commit()
        log_event(
            LOGGER,
            logging.WARNING,
            "Repetição manual de trabalho solicitada.",
            event="graph_job_manual_retry_requested",
            job_id=job_id,
            job_kind=current["kind"],
        )
        self._start_if_enabled()
        return self.status(job_id) or {"id": job_id, "status": "pending"}

    def has_unfinished_upload(self, source: Path) -> bool:
        """Prevent finalization from overtaking publication of this draft."""
        return self.unfinished_upload(source) is not None

    def unfinished_upload(self, source: Path) -> dict[str, Any] | None:
        """Return the latest unfinished publication, including terminal failure."""
        source = Path(source).resolve()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._retire_superseded_uploads(connection)
            rows = connection.execute(
                "SELECT * FROM graph_sync_jobs "
                "WHERE kind = 'upload_active' AND status != 'complete' ORDER BY rowid DESC"
            ).fetchall()
            connection.commit()
        for row in rows:
            payload = self._json_object(row["payload_json"])
            # New jobs point to immutable staging; older jobs use the live path.
            if self._upload_source(payload) == source:
                return self._serialize(row)
        return None

    @staticmethod
    def _upload_source(payload: dict[str, Any]) -> Path | None:
        value = payload.get("_original_draft_path") or payload.get("draft_path")
        if not isinstance(value, str) or not value:
            return None
        try:
            return Path(value).resolve()
        except (OSError, ValueError):
            return None

    def _retire_superseded_uploads(self, connection: sqlite3.Connection) -> None:
        """A later confirmed snapshot supersedes earlier work for the same draft.

        Retain the payload and the explicit replacement ID for audit/rollback.
        Never retire a running worker, an archive, or a different draft.
        """
        rows = connection.execute(
            "SELECT * FROM graph_sync_jobs WHERE kind = 'upload_active' "
            "ORDER BY rowid DESC"
        ).fetchall()
        confirmed: dict[Path, str] = {}
        for row in rows:
            payload = self._json_object(row["payload_json"])
            source = self._upload_source(payload)
            if source is None:
                continue
            if row["status"] == "complete":
                confirmed.setdefault(source, row["id"])
            elif source in confirmed and row["status"] in {"pending", "failed", "held"}:
                result = self._json_object(row["result_json"])
                result["superseded_by"] = confirmed[source]
                connection.execute(
                    "UPDATE graph_sync_jobs SET status = 'complete', retryable = 0, "
                    "result_json = ?, last_error = NULL, updated_at = ? WHERE id = ?",
                    (json.dumps(result), time.time(), row["id"]),
                )

    def _hold_outdated_upload_retries(self, connection: sqlite3.Connection) -> None:
        """Never let an older retry overwrite a newer, partially published save."""
        rows = connection.execute(
            "SELECT * FROM graph_sync_jobs WHERE kind = 'upload_active' "
            "ORDER BY rowid DESC"
        ).fetchall()
        started: dict[Path, str] = {}
        for row in rows:
            payload = self._json_object(row["payload_json"])
            source = self._upload_source(payload)
            if source is None:
                continue
            if source in started and row["status"] in {"pending", "failed", "held"}:
                result = self._json_object(row["result_json"])
                if not result.get("blocked_by_newer_upload"):
                    result["blocked_by_newer_upload"] = started[source]
                    connection.execute(
                        "UPDATE graph_sync_jobs SET status = 'failed', retryable = 0, "
                        "result_json = ?, last_error = ?, updated_at = ? WHERE id = ?",
                        (json.dumps(result), "Existe uma gravação posterior. "
                         "Resolva ou repita a publicação mais recente.", time.time(), row["id"]),
                    )
            if int(row["attempts"]) > 0:
                started.setdefault(source, row["id"])

    def summary(self) -> dict[str, int]:
        result = {"pending": 0, "running": 0, "failed": 0, "complete": 0}
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS total FROM graph_sync_jobs GROUP BY status"
            ).fetchall()
        for row in rows:
            result[str(row["status"])] = int(row["total"])
        return result

    def wait(self, job_id: str, *, timeout: float = 10) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.status(job_id)
            if job and job["status"] in {"complete", "failed"}:
                return job
            time.sleep(0.025)
        return self.status(job_id)

    def _worker(self, *, schedule_follow_up: bool = True) -> None:
        current_job = None
        try:
            while True:
                job = self._claim_next()
                if job is None:
                    return
                current_job = job
                log_event(
                    LOGGER,
                    logging.INFO,
                    "Processamento de trabalho iniciado.",
                    event="graph_job_started",
                    job_id=job["id"],
                    job_kind=job["kind"],
                    attempt=job["attempts"],
                )
                try:
                    result = self._execute(
                        job["kind"],
                        job["payload"],
                        attempts=job["attempts"],
                        job_id=job["id"],
                        prior_result=job.get("result"),
                    )
                except BaseException as exc:
                    self._record_job_failure(job, exc)
                else:
                    try:
                        self._mark_complete(job, result)
                    except BaseException as exc:
                        self._record_job_failure(job, exc)
                current_job = None
        finally:
            if current_job is not None:
                self._hold_job_terminally(
                    current_job,
                    "O worker terminou inesperadamente; o envio requer repetição manual.",
                )
            with self._worker_lock:
                self._worker_running = False
            if schedule_follow_up:
                if self._has_active_job():
                    # Outro worker pode estar a tratar o trabalho. Evita criar quatro
                    # threads por segundo enquanto se aguarda pela reconciliacao stale.
                    delay = 5.0
                elif self._has_due_jobs():
                    self.start()
                    return
                else:
                    delay = self._next_retry_delay()
                if delay is not None:
                    timer = threading.Timer(delay, self.start)
                    timer.daemon = True
                    timer.start()

    def _claim_next(self) -> dict[str, Any] | None:
        now = time.time()
        stale_before = now - max(
            int(os.environ.get("FS_GRAPH_JOB_STALE_SECONDS", "900")),
            120,
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._retire_superseded_uploads(connection)
            self._hold_outdated_upload_retries(connection)
            self._release_committed_jobs(connection)
            self._hold_stale_mail_jobs(connection, now=now)
            active = connection.execute(
                """
                SELECT 1 FROM graph_sync_jobs
                WHERE status = 'running' AND updated_at > ?
                LIMIT 1
                """,
                (stale_before,),
            ).fetchone()
            if active is not None:
                connection.commit()
                return None

            row = connection.execute(
                """
                SELECT * FROM graph_sync_jobs
                WHERE (
                    status = 'pending'
                    OR (status = 'failed' AND retryable = 1 AND next_attempt_at <= ?)
                ) OR (
                    status = 'running' AND updated_at <= ?
                )
                ORDER BY created_at
                LIMIT 1
                """,
                (now, stale_before),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            connection.execute(
                "UPDATE graph_sync_jobs SET status = 'running', attempts = attempts + 1, "
                "updated_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            connection.commit()
        job = self._serialize(row)
        job["status"] = "running"
        job["attempts"] = int(job["attempts"]) + 1
        return job

    def _release_committed_jobs(self, connection: sqlite3.Connection) -> None:
        """Only publish a final archive after its editing transaction committed."""
        rows = connection.execute(
            "SELECT id, payload_json, result_json, updated_at FROM graph_sync_jobs WHERE status = 'held'"
        ).fetchall()
        for row in rows:
            try:
                guard = self._json_object(row["payload_json"]).get("_commit_guard") or {}
                if not isinstance(guard, dict) or not guard:
                    raise ValueError("Invalid commit guard")
                database = Path(str(guard.get("database") or ""))
                if not database.is_file():
                    raise ValueError("Commit database unavailable")
                with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=1) as editing_db:
                    state_row = editing_db.execute(
                        "SELECT state_json FROM editing_states WHERE document_id = ?",
                        (guard.get("document_id"),),
                    ).fetchone()
                state = self._json_object(state_row[0]) if state_row else {}
                operations = state.get("operations", {})
                operation = operations.get(guard.get("operation_id"), {})
                if operation.get("status") == "complete":
                    connection.execute(
                        "UPDATE graph_sync_jobs SET status = 'pending' WHERE id = ?",
                        (row["id"],),
                    )
                    continue
            except Exception:
                # An unknown commit state never authorizes an external effect.
                # Isolate malformed legacy state to this job, not the whole worker.
                pass
            if time.time() - float(row["updated_at"]) >= self.commit_guard_seconds:
                result = self._json_object(row["result_json"])
                result["commit_guard_required"] = True
                connection.execute(
                    "UPDATE graph_sync_jobs SET status = 'failed', retryable = 0, "
                    "last_error = ?, result_json = ?, updated_at = ? WHERE id = ?",
                    ("Confirmação local da finalização em falta ou inválida. "
                     "Requer revisão; repetir volta a verificar a confirmação.",
                     json.dumps(result), time.time(), row["id"]),
                )

    def _execute(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        attempts: int = 0,
        job_id: str = "",
        prior_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if kind == "upload_active":
            if self.graph_service is None:
                raise RuntimeError("O armazenamento Microsoft Graph não está ativo.")
            self._hydrate_staging_metadata(payload)
            try:
                uploaded = self.graph_service.upload_active_bundle(
                    Path(payload["draft_path"]),
                    fail_if_exists=bool(payload.get("fail_if_exists")) and attempts == 1,
                )
            except Exception:
                # Keep confirmed partial writes/ownership for the next snapshot.
                self._publish_staging_metadata(payload, clear_dirty=False)
                raise
            self._publish_staging_metadata(payload)
            return {"uploaded_files": uploaded}
        if kind == "archive_and_remove":
            if self.graph_service is None:
                raise RuntimeError("O armazenamento Microsoft Graph não está ativo.")
            archived_path = Path(payload["archived_path"])
            uploaded = self.graph_service.upload_archive_bundle(archived_path)
            pdf_path = None
            mail_payload = payload.get("mail")
            if mail_payload:
                if self.mail_service is None:
                    raise RuntimeError("O serviço de e-mail não está disponível para este trabalho.")
                self._checkpoint_result(job_id, {"phase": "generating_pdf"})
                pdf_path, remote_pdf = self.graph_service.export_archive_pdf(archived_path)
                self._checkpoint_result(
                    job_id,
                    {"phase": "pdf_ready", "local_pdf": str(pdf_path)},
                )
                uploaded.append(remote_pdf)
            removed = self.graph_service.remove_active_name(
                str(payload["source_name"]),
                expected_etag=payload.get("expected_etag"),
            )
            mail_result = None
            if mail_payload and pdf_path is not None:
                mail_result = self._send_mail_once(
                    dict(mail_payload),
                    pdf_path,
                    job_id=job_id,
                    prior_result=prior_result,
                )
            teams_job = self._enqueue_teams_notification(
                payload.get("teams"),
                parent_job_id=job_id,
            )
            return {
                "uploaded_files": uploaded,
                "removed_active": removed,
                "mail": mail_result,
                "teams_job": teams_job,
            }
        if kind == "archive_and_mail_local":
            if self.mail_service is None:
                raise RuntimeError("O serviço de e-mail não está disponível para este trabalho.")
            if self.local_pdf_service is None:
                raise RuntimeError("O conversor local de PDF não está disponível.")
            archived_path = Path(payload["archived_path"])
            mail_payload = dict(payload.get("mail") or {})
            if not mail_payload:
                raise RuntimeError("Os destinatários do e-mail não foram guardados no trabalho.")
            self._checkpoint_result(job_id, {"phase": "generating_pdf"})
            pdf_path = self.local_pdf_service.export_archive_pdf(archived_path)
            self._checkpoint_result(
                job_id,
                {"phase": "pdf_ready", "local_pdf": str(pdf_path)},
            )
            mail_result = self._send_mail_once(
                mail_payload,
                pdf_path,
                job_id=job_id,
                prior_result=prior_result,
            )
            teams_job = self._enqueue_teams_notification(
                payload.get("teams"),
                parent_job_id=job_id,
            )
            return {
                "uploaded_files": [],
                "removed_active": False,
                "local_pdf": str(pdf_path),
                "mail": mail_result,
                "teams_job": teams_job,
            }
        if kind == "notify_teams":
            if self.teams_notification_service is None:
                raise RuntimeError("O serviço de notificações Teams não está disponível.")
            notification = dict(payload.get("notification") or {})
            if not notification:
                raise RuntimeError("A notificação Teams não foi guardada no trabalho.")
            return self.teams_notification_service.send_prepared(
                notification,
                operation_id=job_id,
            )
        if kind == "remove_active":
            if self.graph_service is None:
                raise RuntimeError("O armazenamento Microsoft Graph não está ativo.")
            removed = self.graph_service.remove_active_name(
                str(payload["source_name"]),
                expected_etag=payload.get("expected_etag"),
            )
            return {"removed_active": removed}
        raise ValueError(f"Tipo de sincronização desconhecido: {kind}")

    def _send_mail_once(
        self,
        mail_payload: dict[str, Any],
        pdf_path: Path,
        *,
        job_id: str,
        prior_result: dict[str, Any] | None,
    ) -> dict[str, Any]:
        previous_mail = dict((prior_result or {}).get("mail") or {})
        if previous_mail.get("accepted"):
            return previous_mail
        if self.mail_service is None:
            raise RuntimeError("O serviço de e-mail não está disponível para este trabalho.")
        self._assert_job_running(job_id)
        self._checkpoint_result(job_id, {"phase": "sending_mail"})
        mail_result = self.mail_service.send_prepared(
            mail_payload,
            pdf_path,
            operation_id=job_id,
        )
        self._checkpoint_result(
            job_id,
            {"phase": "mail_accepted", "mail": mail_result},
        )
        return mail_result

    def _assert_job_running(self, job_id: str) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM graph_sync_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        if row is None or str(row["status"]) != "running":
            raise GraphJobNoLongerActiveError(
                "O trabalho deixou de estar ativo antes do envio; requer repetição manual."
            )

    def _enqueue_teams_notification(
        self,
        notification: Any,
        *,
        parent_job_id: str,
    ) -> dict[str, Any] | None:
        if not notification:
            return None
        if self.teams_notification_service is None:
            raise RuntimeError("O serviço de notificações Teams não está disponível.")
        child_id = f"teams:{parent_job_id}"
        child = self.enqueue(
            "notify_teams",
            {"notification": dict(notification)},
            job_id=child_id,
        )
        return {"id": child_id, "status": child.get("status", "pending")}

    def _checkpoint_result(self, job_id: str, partial_result: dict[str, Any]) -> None:
        now = time.time()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT result_json FROM graph_sync_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            current = json.loads(row["result_json"]) if row and row["result_json"] else {}
            current.update(partial_result)
            connection.execute(
                "UPDATE graph_sync_jobs SET result_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(current, ensure_ascii=False), now, job_id),
            )

    def _mark_complete(self, job: dict[str, Any], result: dict[str, Any]) -> None:
        now = time.time()
        job_id = str(job["id"])
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE graph_sync_jobs
                SET status = 'complete', result_json = ?, last_error = NULL, updated_at = ?
                WHERE id = ?
                """,
                (json.dumps(result, ensure_ascii=False), now, job_id),
            )
            self._retire_superseded_uploads(connection)
            connection.commit()

        self._cleanup_staging(job["payload"])
        log_event(
            LOGGER,
            logging.INFO,
            "Trabalho concluído.",
            event="graph_job_completed",
            job_id=job_id,
            job_kind=job["kind"],
            attempt=job.get("attempts"),
        )

    def _mark_failed(self, job: dict[str, Any], error: BaseException) -> bool:
        attempts = int(job.get("attempts") or 1)
        delay = min(300.0, self.retry_base_seconds * (2 ** min(attempts - 1, 8)))
        retry_after = getattr(error, "retry_after_seconds", None)
        if retry_after is not None:
            delay = min(300.0, max(delay, float(retry_after)))
        now = time.time()
        max_attempts = job.get("max_attempts")
        below_limit = max_attempts is None or attempts < int(max_attempts)
        retryable = bool(getattr(error, "retryable", True)) and below_limit
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE graph_sync_jobs
                SET status = 'failed', attempts = ?, next_attempt_at = ?, retryable = ?,
                    last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    attempts,
                    now + delay,
                    1 if retryable else 0,
                    str(error)[:2000],
                    now,
                    job["id"],
                ),
            )
        return retryable

    def _record_job_failure(self, job: dict[str, Any], error: BaseException) -> None:
        try:
            will_retry = self._mark_failed(job, error)
            log_event(
                LOGGER,
                logging.ERROR,
                "Trabalho falhou durante o processamento.",
                event="graph_job_failed",
                job_id=job.get("id"),
                job_kind=job.get("kind"),
                attempt=job.get("attempts"),
                will_retry=will_retry,
                exc_info=(type(error), error, error.__traceback__),
            )
        except BaseException as state_error:
            log_event(
                LOGGER,
                logging.CRITICAL,
                "Falha ao atualizar o estado de um trabalho com erro.",
                event="graph_job_failure_state_update_failed",
                job_id=job.get("id"),
                job_kind=job.get("kind"),
                exc_info=(type(state_error), state_error, state_error.__traceback__),
            )
            self._hold_job_terminally(
                job,
                f"Falha interna ao atualizar o estado: {error}",
            )

    def _hold_job_terminally(self, job: dict[str, Any], message: str) -> None:
        now = time.time()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE graph_sync_jobs
                    SET status = 'failed', retryable = 0, next_attempt_at = ?,
                        last_error = ?, updated_at = ?
                    WHERE id = ? AND status = 'running'
                    """,
                    (now, str(message)[:2000], now, job["id"]),
                )
        except BaseException as state_error:
            log_event(
                LOGGER,
                logging.CRITICAL,
                "Não foi possível colocar um trabalho em pausa.",
                event="graph_job_terminal_hold_failed",
                job_id=job.get("id"),
                job_kind=job.get("kind"),
                exc_info=(type(state_error), state_error, state_error.__traceback__),
            )

    def _hold_stale_mail_jobs(
        self,
        connection: sqlite3.Connection,
        *,
        now: float,
    ) -> None:
        stale_before = now - self.mail_job_stale_seconds
        rows = connection.execute(
            """
            SELECT id, kind, payload_json, result_json
            FROM graph_sync_jobs
            WHERE status = 'running' AND updated_at <= ?
            """,
            (stale_before,),
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                payload = {}
            has_mail = row["kind"] == "archive_and_mail_local" or bool(payload.get("mail"))
            if not has_mail:
                continue
            try:
                result = json.loads(row["result_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                result = {}
            if dict(result.get("mail") or {}).get("accepted"):
                cursor = connection.execute(
                    """
                    UPDATE graph_sync_jobs
                    SET status = 'complete', retryable = 0, last_error = NULL,
                        updated_at = ?
                    WHERE id = ? AND status = 'running'
                    """,
                    (now, row["id"]),
                )
                if cursor.rowcount:
                    log_event(
                        LOGGER,
                        logging.INFO,
                        "Trabalho inativo reconciliado como concluído.",
                        event="graph_job_stale_reconciled",
                        job_id=row["id"],
                        job_kind=row["kind"],
                    )
                continue
            cursor = connection.execute(
                """
                UPDATE graph_sync_jobs
                SET status = 'failed', retryable = 0, next_attempt_at = ?,
                    last_error = ?, updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (
                    now,
                    "O processamento ficou sem atividade; o envio não foi confirmado "
                    "e requer repetição manual.",
                    now,
                    row["id"],
                ),
            )
            if cursor.rowcount:
                log_event(
                    LOGGER,
                    logging.WARNING,
                    "Trabalho inativo retido para revisão manual.",
                    event="graph_job_stale_held",
                    job_id=row["id"],
                    job_kind=row["kind"],
                )

    def _has_due_jobs(self) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM graph_sync_jobs
                WHERE status = 'pending'
                   OR (status = 'failed' AND retryable = 1 AND next_attempt_at <= ?)
                LIMIT 1
                """,
                (time.time(),),
            ).fetchone()
        return row is not None

    def _has_active_job(self) -> bool:
        stale_before = time.time() - max(
            int(os.environ.get("FS_GRAPH_JOB_STALE_SECONDS", "900")),
            120,
        )
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM graph_sync_jobs "
                "WHERE status = 'running' AND updated_at > ? LIMIT 1",
                (stale_before,),
            ).fetchone()
        return row is not None

    def _next_retry_delay(self) -> float | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT MIN(next_attempt_at) AS due_at FROM graph_sync_jobs "
                "WHERE status = 'failed' AND retryable = 1"
            ).fetchone()
        due_at = row["due_at"] if row else None
        if due_at is None:
            with self._connect() as connection:
                held = connection.execute(
                    "SELECT 1 FROM graph_sync_jobs WHERE status = 'held' LIMIT 1"
                ).fetchone()
            return 5.0 if held else None
        return max(float(due_at) - time.time(), 0.05)

    def _stage_payload(
        self,
        job_id: str,
        kind: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        prepared = dict(payload)
        if kind != "upload_active":
            return prepared

        source = Path(str(payload["draft_path"]))
        if not source.exists():
            raise FileNotFoundError(f"Rascunho local não encontrado: {source}")
        if source.parent.name != source.stem:
            raise ValueError("O rascunho a publicar não está numa pasta isolada.")
        if not dirty_version(source.parent):
            mark_dirty(source.parent)

        safe_id = hashlib.sha256(job_id.encode("utf-8")).hexdigest()[:16]
        target = self.asset_root / safe_id
        temporary = self.asset_root / f".{safe_id}.{uuid.uuid4().hex[:8]}.tmp"
        if temporary.exists():
            shutil.rmtree(temporary)
        temporary.mkdir(parents=True)
        shutil.copytree(source.parent, temporary / source.parent.name)
        if target.exists():
            shutil.rmtree(target)
        os.replace(str(temporary), str(target))

        staged_source = target / source.parent.name / source.name
        prepared["_original_draft_path"] = str(source)
        prepared["draft_path"] = str(staged_source)
        prepared["_staging_dir"] = str(target)
        return prepared

    @staticmethod
    def _metadata_files(directory: Path) -> list[Path]:
        if not directory.exists():
            return []
        return [
            path
            for path in directory.rglob("*")
            if path.is_file()
            and (path.name in {".graph_bundle.json", ".fs-upload-owner.json"}
                 or path.name.endswith(".graph.json"))
            and path.name != DIRTY_MARKER + ".graph.json"
        ]

    def _hydrate_staging_metadata(self, payload: dict[str, Any]) -> None:
        staged = Path(str(payload["draft_path"]))
        original_value = str(payload.get("_original_draft_path") or "")
        if not original_value:
            return
        original = Path(original_value)
        if not original.parent.exists() or not staged.parent.exists():
            return
        with bundle_guard(original.parent):
            self._copy_newer_metadata(original.parent, staged.parent)

    def _publish_staging_metadata(self, payload: dict[str, Any], *, clear_dirty: bool = True) -> None:
        staged = Path(str(payload["draft_path"]))
        original_value = str(payload.get("_original_draft_path") or "")
        if not original_value:
            return
        original = Path(original_value)
        if not original.parent.exists() or not staged.parent.exists():
            return
        with bundle_guard(original.parent):
            self._publish_locked_metadata(staged, original, clear_dirty=clear_dirty)

    def _copy_newer_metadata(self, source_dir: Path, destination_dir: Path) -> None:
        for source in self._metadata_files(source_dir):
            destination = destination_dir / source.relative_to(source_dir)
            if destination.exists() and source.stat().st_mtime_ns <= destination.stat().st_mtime_ns:
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex[:8]}.tmp")
            shutil.copy2(source, temporary)
            os.replace(str(temporary), str(destination))

    def _publish_locked_metadata(self, staged: Path, original: Path, *, clear_dirty: bool = True) -> None:
        self._copy_newer_metadata(staged.parent, original.parent)
        staged_version = dirty_version(staged.parent)
        if clear_dirty and staged_version and dirty_version(original.parent) == staged_version:
            (original.parent / DIRTY_MARKER).unlink(missing_ok=True)


    def _cleanup_staging(self, payload: dict[str, Any]) -> None:
        value = str(payload.get("_staging_dir") or "")
        if not value:
            return
        candidate = Path(value).resolve()
        root = self.asset_root.resolve()
        if candidate == root or not candidate.is_relative_to(root):
            return
        shutil.rmtree(candidate, ignore_errors=True)
    @staticmethod
    def _json_object(raw: str | None) -> dict[str, Any]:
        try:
            value = json.loads(raw or "{}")
            return value if isinstance(value, dict) else {}
        except (TypeError, ValueError):
            return {}

    @staticmethod
    def _serialize(row: sqlite3.Row) -> dict[str, Any]:
        status = str(row["status"])
        will_retry = status == "failed" and bool(row["retryable"])
        return {
            "id": str(row["id"]),
            "kind": str(row["kind"]),
            "payload": GraphSyncQueue._json_object(row["payload_json"]),
            "status": status,
            "will_retry": will_retry,
            "attempts": int(row["attempts"]),
            "max_attempts": (
                int(row["max_attempts"]) if row["max_attempts"] is not None else None
            ),
            "next_attempt_at": float(row["next_attempt_at"]),
            "last_error": row["last_error"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
        }
