"""Persistent transactional outbox for Microsoft Graph operations."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from src.logging_config import log_event
from src.services.graph_mail_service import (
    GraphMailService, GraphMailDelivery, GraphMailError, GraphMailUnknownResultError,
    MAX_SIMPLE_REQUEST_BYTES,
)
from src.services.graph_storage_service import GraphStorageService, GraphConflictError
from src.services.local_pdf_service import LocalPdfService
from src.services.teams_notification_service import (
    TeamsNotificationService, TeamsNotificationConfigurationError,
    TeamsNotificationError, TeamsNotificationUnknownResultError,
)
from src.services.local_changes import DIRTY_MARKER, bundle_guard, dirty_version, mark_dirty
from src.services.bundle_removals import copy_confirmations, refresh_own_removal_versions
from src.services.file_mutex import file_mutex, FileMutexBusy
from src.services.atomic_files import replace_local
from src.services.runtime_admission import admitted, operation_for
from src.services.runtime_contract import ContractError, ContractBusy
from src.services.sqlite_storage_format import initialize_store, validate_store, StorageFormatError
from src.services.editing_state_format import decode_editing_state
from src.services.graph_job_visibility import normalize_job_owner
from src.services.diagnostic_views import safe_provider_failure


LOGGER = logging.getLogger(__name__)


def delivery_uncertain(kind: str, result: dict[str, Any]) -> bool:
    # Malformed legacy evidence cannot prove whether an external effect occurred.
    # Migration preserves its raw text separately and explicitly requires review.
    if (result.get("migration_uncertain") is True or result.get("restore_uncertain") is True
            or result.get("reconciliation_hold") is True):
        return True
    if kind == "remove_active":
        return result.get("cancellation_started") is True and result.get("cancellation_confirmed") is not True
    if kind == "notify_teams":
        return (result.get("phase") in {"sending_teams", "teams_unknown"}
                and result.get("accepted") is not True)
    mail = result.get("mail") if isinstance(result.get("mail"), dict) else {}
    return result.get("phase") in {"sending_mail", "mail_unknown"} and mail.get("accepted") is not True


def teams_retry_deadline(kind: str, result: dict[str, Any]) -> float | None:
    # Reconciled provider refusals also carry a binding mail retry deadline.
    value = result.get("retry_not_before") if kind == "notify_teams" or result.get("reconciled_retry_deadline") is True else None
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError("Prazo Teams inválido; requer revisão.")
    try:
        deadline = float(value)
    except OverflowError:
        raise ValueError("Prazo Teams inválido; requer revisão.") from None
    if not math.isfinite(deadline):
        raise ValueError("Prazo Teams inválido; requer revisão.")
    return deadline


class GraphJobNoLongerActiveError(RuntimeError):
    """Impede que um worker órfão continue até um envio externo."""

    retryable = False


class MailSnapshotError(GraphMailError):
    """A persisted message cannot be reconstructed or replayed safely."""


class CancellationUnknownResultError(RuntimeError):
    """The current attempt must fail without replaying an uncertain move."""

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
        runtime_contract=None,
    ):
        self.runtime_contract = runtime_contract
        self.graph_service = graph_service
        self.mail_service = mail_service
        self.local_pdf_service = local_pdf_service
        self.teams_notification_service = teams_notification_service
        self.database_path = Path(database_path)
        self.asset_root = self.database_path.parent / "graph-sync-assets"
        self.busy_timeout_ms = max(int(os.environ.get("FS_STATE_DB_BUSY_MS", "10000")), 1000)
        self.mail_job_max_attempts = max(
            int(os.environ.get("FS_MAIL_JOB_MAX_ATTEMPTS", "3")),
            1,
        )
        self.teams_job_max_attempts = max(int(os.environ.get("FS_TEAMS_JOB_MAX_ATTEMPTS", "3")), 1)
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
        self._attempt_context = threading.local()
        self._worker_mutex_path = self.database_path.with_name(self.database_path.name + ".worker.lock")
        self.heartbeat_seconds = min(30.0, self.mail_job_stale_seconds / 3)
        self._initialize()
        self._start_if_enabled()

    def _connect(self, *, check_format=True) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
            connection.execute("PRAGMA synchronous = FULL")
            if check_format:
                validate_store(connection, "queue")
        except sqlite3.Error as exc:
            connection.close()
            if (getattr(exc, "sqlite_errorcode", 0) & 255) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                raise
            raise StorageFormatError("Base SQLite de queue ilegível; requer diagnóstico.") from exc
        except BaseException:
            connection.close()
            raise
        return connection

    @admitted
    def _initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + (self.busy_timeout_ms / 1000)
        while True:
            try:
                with self._connect(check_format=False) as connection:
                    initialize_store(connection, "queue")
                    connection.execute("PRAGMA journal_mode = WAL")
                    connection.execute("BEGIN IMMEDIATE")
                    self._quarantine_invalid_jobs(connection)
                self.asset_root.mkdir(parents=True, exist_ok=True)
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).casefold() or time.monotonic() >= deadline:
                    raise
                time.sleep(0.025)

    @admitted
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
        return self._worker(schedule_follow_up=False)

    def _job_max_attempts(self, kind: str, payload: dict[str, Any]) -> int | None:
        if kind == "notify_teams":
            return self.teams_job_max_attempts
        has_mail = kind == "archive_and_mail_local" or bool(payload.get("mail"))
        return self.mail_job_max_attempts if has_mail else None

    @admitted
    def status(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._quarantine_invalid_jobs(connection)
            try:
                with file_mutex(self._worker_mutex_path):
                    self._hold_stale_mail_jobs(connection, now=time.time())
            except FileMutexBusy:
                # An active process, even during a slow transport, is not stale.
                pass
            row = connection.execute(
                "SELECT * FROM graph_sync_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._serialize(row) if row else None

    @admitted
    def reconciliation(self, job_id: str, *, actor) -> dict[str, Any]:
        from src.services.admin_access import can_administer
        from src.services.job_reconciliation import inspection
        if not can_administer(actor):
            raise PermissionError("Acesso reservado ao administrador.")
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM graph_sync_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            return inspection(row)

    @admitted
    def reconcile(self, job_id: str, **decision) -> dict[str, Any]:
        from src.services.job_reconciliation import reconcile
        return reconcile(self, job_id, **decision)

    @admitted
    def retry(self, job_id: str) -> dict[str, Any]:
        """Repete explicitamente um único trabalho que ficou terminalmente falhado."""
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._quarantine_invalid_jobs(connection)
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
            if (current["result"] or {}).get("invalid_state"):
                connection.commit()
                raise ValueError("Dados do trabalho inválidos. É necessário corrigir o estado antes de repetir.")
            if current["reconciliation_required"]:
                connection.rollback()
                raise ValueError("Resultado do envio desconhecido. É necessária reconciliação antes de repetir.")
            deadline = teams_retry_deadline(current["kind"], current["result"] or {})
            if deadline is not None and now < deadline:
                connection.rollback()
                raise ValueError("O prazo indicado pelo fornecedor ainda não terminou. Atualize o diagnóstico mais tarde.")
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
            self._quarantine_invalid_jobs(connection)
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
        result = {"pending": 0, "running": 0, "failed": 0, "complete": 0,
                  "teams_missing": 0, "teams_conflicts": 0}
        with self._connect() as connection:
            connection.execute("BEGIN")
            rows = connection.execute(
                "SELECT status, COUNT(*) AS total FROM graph_sync_jobs GROUP BY status"
            ).fetchall()
            parents = connection.execute("SELECT p.payload_json,p.result_json,c.id AS child_id "
                "FROM graph_sync_jobs p LEFT JOIN graph_sync_jobs c ON c.id = 'teams:' || p.id "
                "WHERE p.status='complete' AND p.kind IN ('archive_and_remove','archive_and_mail_local')")
            for parent in parents:
                payload = self._json_object(parent["payload_json"])
                outcome = self._json_object(parent["result_json"])
                if outcome.get("teams_child_conflict"):
                    result["teams_conflicts"] += 1
                if payload.get("teams") and parent["child_id"] is None:
                    result["teams_missing"] += 1
        for row in rows:
            result[str(row["status"])] = int(row["total"])
        return result

    @admitted
    def diagnostic_backlog(self) -> dict:
        """Read backlog age independently of cycle counters (which must stay stable)."""
        now = time.time()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT MIN(CASE WHEN typeof(created_at) IN ('integer','real') AND created_at>=0 "
                "AND created_at<=? THEN created_at END) AS oldest, "
                "COUNT(CASE WHEN kind='notify_teams' THEN 1 END) AS teams_unfinished "
                "FROM graph_sync_jobs WHERE status!='complete'", (now,),
            ).fetchone()
        return {"oldest_unfinished_age_seconds": now - row["oldest"] if row["oldest"] is not None else None,
                "teams_unfinished": int(row["teams_unfinished"])}

    def wait(self, job_id: str, *, timeout: float = 10) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.status(job_id)
            if job and job["status"] in {"complete", "failed"}:
                return job
            time.sleep(0.025)
        return self.status(job_id)

    def _worker(self, *, schedule_follow_up: bool = True) -> bool:
        admission_busy = False
        try:
            with operation_for(self), file_mutex(self._worker_mutex_path):
                self._process_jobs()
                return True
        except ContractBusy:
            if not schedule_follow_up:
                raise
            admission_busy = True
            LOGGER.info("Worker adiado: contrato temporariamente ocupado.")
        except ContractError:
            synchronous = not schedule_follow_up
            schedule_follow_up = False
            if synchronous:
                raise
            LOGGER.warning("Worker recusado pelo contrato de execução; reiniciar na versão ativa.")
        except FileMutexBusy:
            return False
        finally:
            with self._worker_lock:
                self._worker_running = False
            if schedule_follow_up:
                if admission_busy:
                    delay = 1.0
                elif self._has_active_job():
                    delay = 5.0
                elif self._has_due_jobs():
                    # A busy process lock is also possible when its SQLite lease
                    # looks expired. Back off instead of spawning a tight loop.
                    delay = 0.25
                else:
                    delay = self._next_retry_delay()
                if delay is not None:
                    timer = threading.Timer(delay, self.start)
                    timer.daemon = True
                    timer.start()

    def _process_jobs(self) -> None:
        current_job = None
        try:
            while True:
                job = self._claim_next()
                if job is None:
                    return
                current_job = job
                self._attempt_context.job = job
                stop = threading.Event()
                heartbeat = threading.Thread(target=self._keep_alive, args=(job, stop), daemon=True,
                                             name="graph-sync-heartbeat")
                heartbeat.start()
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
                    try:
                        result = self._execute(
                            job["kind"], job["payload"], attempts=job["attempts"],
                            job_id=job["id"], prior_result=job.get("result"),
                        )
                    except BaseException as exc:
                        self._record_job_failure(job, exc)
                    else:
                        try:
                            self._mark_complete(job, result)
                        except BaseException as exc:
                            self._record_job_failure(job, exc)
                finally:
                    stop.set()
                    heartbeat.join(timeout=1)
                    self._attempt_context.job = None
                current_job = None
        finally:
            if current_job is not None:
                self._hold_job_terminally(
                    current_job,
                    "O worker terminou inesperadamente; o envio requer repetição manual.",
                )

    @admitted
    def _heartbeat(self, job: dict[str, Any]) -> bool:
        with self._connect() as connection:
            changed = connection.execute(
                "UPDATE graph_sync_jobs SET updated_at=? WHERE id=? AND status='running' AND attempt_token=?",
                (time.time(), job["id"], job["attempt_token"]),
            )
        return bool(changed.rowcount)

    def _keep_alive(self, job: dict[str, Any], stop: threading.Event) -> None:
        while not stop.wait(self.heartbeat_seconds):
            try:
                if not self._heartbeat(job):
                    return
            except ContractBusy:
                LOGGER.info("Heartbeat adiado: contrato temporariamente ocupado.")
            except ContractError:
                return
            except sqlite3.Error:
                # The process lock still prevents overlapping external effects.
                LOGGER.warning("Não foi possível atualizar a atividade do worker.")

    def _claim_next(self) -> dict[str, Any] | None:
        now = time.time()
        stale_before = now - max(
            int(os.environ.get("FS_GRAPH_JOB_STALE_SECONDS", "900")),
            120,
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._quarantine_invalid_jobs(connection)
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
            attempt_token = uuid.uuid4().hex
            connection.execute(
                "UPDATE graph_sync_jobs SET status = 'running', attempts = attempts + 1, "
                "updated_at = ?, attempt_token = ? WHERE id = ?",
                (now, attempt_token, row["id"]),
            )
            connection.commit()
        job = self._serialize(row)
        job["status"] = "running"
        job["attempts"] = int(job["attempts"]) + 1
        job["attempt_token"] = attempt_token
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
                    validate_store(editing_db, "editing")
                    state_row = editing_db.execute(
                        "SELECT state_json FROM editing_states WHERE document_id = ?",
                        (guard.get("document_id"),),
                    ).fetchone()
                state = decode_editing_state(state_row[0], guard.get("document_id")) if state_row else {}
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
        self._assert_job_running(job_id)
        prior_result = dict(prior_result or {})
        if (prior_result or {}).get("phase") in {"sending_mail", "mail_unknown"} and not (
            (prior_result or {}).get("mail") or {}
        ).get("accepted"):
            # Do not erase uncertainty with PDF/upload checkpoints on recovery.
            raise GraphMailUnknownResultError("Envio anterior sem resultado confirmado; requer reconciliação.")
        if kind in {"archive_and_remove", "archive_and_mail_local"} and (
            prior_result.get("mail") or {}
        ).get("accepted"):
            # Receipts take precedence over missing/retired message bytes and
            # changed configuration. Never rebuild or repeat accepted mail.
            return prior_result
        if kind == "upload_active":
            if self.graph_service is None:
                raise RuntimeError("O armazenamento Microsoft Graph não está ativo.")
            self._hydrate_staging_metadata(payload)
            self._assert_job_running(job_id)
            try:
                uploaded = self.graph_service.upload_active_bundle(
                    Path(payload["draft_path"]),
                    fail_if_exists=bool(payload.get("fail_if_exists")) and attempts == 1,
                )
            except Exception:
                # Keep confirmed partial writes/ownership for the next snapshot.
                self._assert_job_running(job_id)
                self._publish_staging_metadata(payload, clear_dirty=False)
                raise
            self._assert_job_running(job_id)
            self._publish_staging_metadata(payload)
            return {"uploaded_files": uploaded}
        if kind == "archive_and_remove":
            if self.graph_service is None:
                raise RuntimeError("O armazenamento Microsoft Graph não está ativo.")
            archived_path = Path(payload["archived_path"])
            mail_payload = payload.get("mail")
            if mail_payload and self.mail_service is None:
                raise GraphMailError("O serviço de e-mail não está disponível para este trabalho.")
            delivery = self._load_mail_delivery(job_id, prior_result) if mail_payload else None
            if delivery is None:
                if mail_payload:
                    # Persist before the first upload: a failure/crash here has
                    # not attempted mail. Missing legacy phases remain unknown;
                    # never infer this checkpoint when merely inspecting a job.
                    self._checkpoint_result(job_id, {"phase": "uploading_archive"})
                uploaded = self.graph_service.upload_archive_bundle(archived_path)
                if mail_payload:
                    self._checkpoint_result(job_id, {"phase": "generating_pdf"})
                    pdf_path, remote_pdf = self.graph_service.export_archive_pdf(archived_path)
                    self._checkpoint_result(job_id, {"phase": "pdf_ready", "local_pdf": str(pdf_path)})
                    uploaded.append(remote_pdf)
                    delivery = self._prepare_mail_delivery(dict(mail_payload), pdf_path, job_id=job_id)
                    prior_result = self._save_mail_delivery(job_id, delivery,
                        {"uploaded_files": uploaded, "local_pdf": str(pdf_path)})
            else:
                uploaded = prior_result.get("uploaded_files", [])
            if delivery is not None:
                self._validate_fixed_mail(job_id, delivery)
            self._assert_job_running(job_id)
            if prior_result.get("active_removed") is True:
                removed = prior_result["removed_active"]
            else:
                etag = payload.get("expected_etag")
                if prior_result.get("removal_started") and not (isinstance(etag, str) and etag.strip()):
                    self._checkpoint_result(job_id, {"phase": "removal_unknown", "invalid_state": True})
                    raise GraphMailError("Remoção anterior sem confirmação e sem eTag; requer reconciliação.")
                prior_result.update(phase="removing_active", removal_started=True)
                self._checkpoint_result(job_id, prior_result)
                removed = self.graph_service.remove_active_name(
                    str(payload["source_name"]), expected_etag=etag)
                if type(removed) is not bool:
                    raise GraphMailError("Resposta inválida ao confirmar a remoção do ativo.")
                prior_result.update(phase="active_removed", active_removed=True, removed_active=removed)
                self._checkpoint_result(job_id, prior_result)
            mail_result = None
            if delivery is not None:
                mail_result = self._send_mail_once(
                    delivery,
                    job_id=job_id,
                    prior_result=prior_result,
                )
                prior_result["phase"] = "mail_accepted"
            return {
                **prior_result,
                "uploaded_files": uploaded,
                "removed_active": removed,
                "mail": mail_result,
            }
        if kind == "archive_and_mail_local":
            if self.mail_service is None:
                raise GraphMailError("O serviço de e-mail não está disponível para este trabalho.")
            archived_path = Path(payload["archived_path"])
            mail_payload = dict(payload.get("mail") or {})
            if not mail_payload:
                raise RuntimeError("Os destinatários do e-mail não foram guardados no trabalho.")
            delivery = self._load_mail_delivery(job_id, prior_result)
            if delivery is None:
                if self.local_pdf_service is None:
                    raise GraphMailError("O conversor local de PDF não está disponível.")
                self._checkpoint_result(job_id, {"phase": "generating_pdf"})
                pdf_path = self.local_pdf_service.export_archive_pdf(archived_path)
                self._checkpoint_result(job_id, {"phase": "pdf_ready", "local_pdf": str(pdf_path)})
                delivery = self._prepare_mail_delivery(mail_payload, pdf_path, job_id=job_id)
                prior_result = self._save_mail_delivery(job_id, delivery,
                    {"uploaded_files": [], "local_pdf": str(pdf_path)})
            self._validate_fixed_mail(job_id, delivery)
            mail_result = self._send_mail_once(
                delivery,
                job_id=job_id,
                prior_result=prior_result,
            )
            prior_result["phase"] = "mail_accepted"
            return {
                **prior_result,
                "uploaded_files": [],
                "removed_active": False,
                "local_pdf": prior_result.get("local_pdf"),
                "mail": mail_result,
            }
        if kind == "notify_teams":
            return self._send_teams_once(payload.get("notification"), job_id=job_id, prior_result=prior_result)
        if kind == "remove_active":
            if self.graph_service is None:
                raise RuntimeError("O armazenamento Microsoft Graph não está ativo.")
            if prior_result.get("cancellation_confirmed") is True:
                return prior_result
            if delivery_uncertain(kind, prior_result):
                # This attempt still owns the job. Record a terminal failure even
                # when reclaim and mail-hold deadlines differ; an orphan error
                # would be ignored and leave the queue blocked by a running job.
                raise CancellationUnknownResultError("Cancelamento remoto incerto; não repetir sem reconciliação.")
            if not isinstance(payload.get("cancellation"), dict) or not payload.get("_commit_guard"):
                self._checkpoint_result(job_id, {"invalid_state": True, "phase": "cancellation_proof_missing"})
                raise ValueError("Cancelamento antigo sem prova de identidade/commit; requer reconciliação.")
            from src.services.graph_cancellation import execute_cancellation
            # Preflight failures remain retryable. Only an attempted PATCH has
            # an uncertain effect; a definite 409/412 is a recorded refusal.
            request_id = str(uuid.uuid4())
            def before_write():
                self._checkpoint_result(job_id, {"phase": "canceling_remote", "cancellation_started": True,
                                                 "delivery_request_id": request_id})
            try:
                outcome = execute_cancellation(self.graph_service, payload["cancellation"], before_write=before_write, request_id=request_id)
            except GraphConflictError as exc:
                if exc.status_code in {409, 412}:
                    self._checkpoint_result(job_id, {"phase": "cancellation_rejected", "cancellation_started": False})
                raise
            outcome.update(phase="cancellation_confirmed", cancellation_started=True, cancellation_confirmed=True)
            self._checkpoint_result(job_id, outcome)
            return outcome
        raise ValueError(f"Tipo de sincronização desconhecido: {kind}")

    @staticmethod
    def _mail_digest(job_id, delivery, created_attempt):
        metadata = json.dumps([1, job_id, delivery.sender, delivery.tenant_id,
                               delivery.test_recipient, created_attempt],
                              ensure_ascii=True, separators=(",", ":")).encode("ascii")
        return hashlib.sha256(metadata + b"\n" + delivery.payload).hexdigest()

    def _save_mail_delivery(self, job_id, delivery, progress):
        self._assert_job_running(job_id)
        self._validate_fixed_mail(job_id, delivery)
        token = self._attempt_context.job["attempt_token"]
        digest = self._mail_digest(job_id, delivery, token)
        marker = {"format": 1, "sha256": digest, "size": len(delivery.payload)}
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT result_json FROM graph_sync_jobs "
                "WHERE id=? AND status='running' AND attempt_token=?", (job_id, token)).fetchone()
            if row is None:
                raise GraphJobNoLongerActiveError("A tentativa do worker já não está ativa.")
            current = self._json_object(row["result_json"])
            if (current.get("mail_snapshot") or (current.get("mail") or {}).get("accepted")
                    or current.get("phase") in {"sending_mail", "mail_unknown"}
                    or connection.execute("SELECT 1 FROM graph_mail_messages WHERE job_id=?", (job_id,)).fetchone()):
                raise MailSnapshotError("Já existe uma mensagem ou resultado fixado; requer revisão.")
            connection.execute("INSERT INTO graph_mail_messages "
                "(job_id,payload,sender,tenant_id,test_recipient,sha256,byte_count,created_attempt) "
                "VALUES (?,?,?,?,?,?,?,?)", (job_id, delivery.payload, delivery.sender, delivery.tenant_id,
                delivery.test_recipient, digest, len(delivery.payload), token))
            current.update(progress)
            current.update(phase="message_ready", mail_snapshot=marker)
            connection.execute("UPDATE graph_sync_jobs SET result_json=?, updated_at=? "
                "WHERE id=? AND status='running' AND attempt_token=?",
                (json.dumps(current, ensure_ascii=False), time.time(), job_id, token))
        return current

    def _load_mail_delivery(self, job_id, previous):
        self._assert_job_running(job_id)
        expected = previous.get("mail_snapshot")
        try:
            with self._connect() as connection:
                connection.execute("BEGIN")
                row = connection.execute("SELECT sender,tenant_id,test_recipient,sha256,byte_count,"
                    "created_attempt,retired_at,length(payload) AS actual_bytes,typeof(payload) AS payload_type "
                    "FROM graph_mail_messages WHERE job_id=?", (job_id,)).fetchone()
                if row is None:
                    if (expected is not None or previous.get("active_removed") is not None
                            or previous.get("removal_started") is not None
                            or previous.get("phase") in {"message_ready", "removing_active", "active_removed"}):
                        raise MailSnapshotError("Mensagem preparada em falta; requer revisão sem reconstrução.")
                    return None
                if (not isinstance(expected, dict) or type(expected.get("format")) is not int
                        or expected["format"] != 1 or type(expected.get("size")) is not int
                        or row["payload_type"] != "blob" or row["retired_at"] is not None
                        or type(row["actual_bytes"]) is not int or not 0 < row["actual_bytes"] <= MAX_SIMPLE_REQUEST_BYTES
                        or row["byte_count"] != row["actual_bytes"] or expected["size"] != row["actual_bytes"]
                        or not isinstance(row["sha256"], str) or len(row["sha256"]) != 64
                        or expected.get("sha256") != row["sha256"]
                        or any(not isinstance(row[key], str) for key in
                               ("sender", "tenant_id", "test_recipient", "created_attempt"))):
                    raise MailSnapshotError("Metadados da mensagem preparada inválidos; requer revisão.")
                payload = connection.execute("SELECT payload FROM graph_mail_messages WHERE job_id=?", (job_id,)).fetchone()[0]
                delivery = GraphMailDelivery(payload, row["sender"], row["tenant_id"], row["test_recipient"])
                if self._mail_digest(job_id, delivery, row["created_attempt"]) != row["sha256"]:
                    raise MailSnapshotError("Mensagem preparada alterada; requer revisão sem reconstrução.")
                if ("active_removed" in previous and type(previous["active_removed"]) is not bool
                        or "removal_started" in previous and type(previous["removal_started"]) is not bool
                        or previous.get("active_removed") is True and type(previous.get("removed_active")) is not bool):
                    raise MailSnapshotError("Confirmação de remoção inválida; requer revisão.")
            return delivery
        except MailSnapshotError:
            self._checkpoint_result(job_id, {"phase": "mail_snapshot_invalid", "invalid_state": True})
            raise

    def _validate_fixed_mail(self, job_id, delivery):
        try:
            self.mail_service.validate_delivery(delivery)
        except GraphMailError:
            self._checkpoint_result(job_id, {"phase": "mail_rejected"})
            raise

    def _prepare_mail_delivery(
        self, mail_payload: dict[str, Any], pdf_path: Path, *, job_id: str,
    ) -> GraphMailDelivery:
        self._assert_job_running(job_id)
        try:
            return self.mail_service.prepare_delivery(mail_payload, pdf_path, operation_id=job_id)
        except GraphMailError:
            self._checkpoint_result(job_id, {"phase": "mail_rejected"})
            raise
        except Exception as exc:
            self._checkpoint_result(job_id, {"phase": "mail_rejected"})
            raise GraphMailError("Não foi possível preparar a mensagem; nenhum envio foi tentado.") from exc

    def _send_mail_once(
        self,
        delivery: GraphMailDelivery,
        *,
        job_id: str,
        prior_result: dict[str, Any] | None,
    ) -> dict[str, Any]:
        previous_mail = dict((prior_result or {}).get("mail") or {})
        if previous_mail.get("accepted"):
            return previous_mail
        if (prior_result or {}).get("phase") in {"sending_mail", "mail_unknown"}:
            raise GraphMailUnknownResultError("Envio anterior sem resultado confirmado; requer reconciliação.")
        if self.mail_service is None:
            raise RuntimeError("O serviço de e-mail não está disponível para este trabalho.")
        self._assert_job_running(job_id)
        self._validate_fixed_mail(job_id, delivery)
        request_id = str(uuid.uuid4())
        self._checkpoint_result(job_id, {"phase": "sending_mail", "delivery_request_id": request_id})
        try:
            mail_result = self.mail_service.send_delivery(delivery, request_id=request_id)
        except GraphMailUnknownResultError:
            self._checkpoint_result(job_id, {"phase": "mail_unknown"})
            raise
        except GraphMailError:
            self._checkpoint_result(job_id, {"phase": "mail_rejected"})
            raise
        except Exception as exc:
            self._checkpoint_result(job_id, {"phase": "mail_unknown"})
            raise GraphMailUnknownResultError("Resultado do envio desconhecido; requer reconciliação.") from exc
        self._checkpoint_result(
            job_id,
            {"phase": "mail_accepted", "mail": mail_result},
        )
        return mail_result

    def _assert_job_running(self, job_id: str) -> None:
        job = getattr(self._attempt_context, "job", None)
        if not job or job["id"] != job_id:
            raise GraphJobNoLongerActiveError("Tentativa do worker não identificada.")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM graph_sync_jobs WHERE id = ? AND attempt_token = ?",
                (job_id, job["attempt_token"]),
            ).fetchone()
        if row is None or str(row["status"]) != "running":
            raise GraphJobNoLongerActiveError(
                "O trabalho deixou de estar ativo antes do envio; requer repetição manual."
            )

    def _send_teams_once(self, notification, *, job_id, prior_result):
        previous = prior_result or {}
        if previous.get("accepted") is True:
            return previous
        if delivery_uncertain("notify_teams", previous):
            raise TeamsNotificationUnknownResultError("Resultado Teams desconhecido; requer reconciliação.")
        service = self.teams_notification_service
        try:
            if service is None:
                raise TeamsNotificationConfigurationError("O serviço de notificações Teams não está disponível.")
            service.validate_prepared(notification)
        except TeamsNotificationError as exc:
            self._checkpoint_result(job_id, {"phase": "teams_rejected", "http_status": exc.http_status,
                                            "retry_not_before": exc.retry_not_before})
            raise
        request_id = str(uuid.uuid4())
        self._checkpoint_result(job_id, {
            "phase": "sending_teams", "http_status": None, "retry_not_before": None,
            "delivery_request_id": request_id,
        })
        try:
            receipt = service.send_prepared(notification, operation_id=job_id, request_id=request_id)
            if not isinstance(receipt, dict) or receipt.get("accepted") is not True:
                raise TeamsNotificationUnknownResultError("Resultado Teams inválido; requer reconciliação.")
        except TeamsNotificationUnknownResultError:
            self._checkpoint_result(job_id, {"phase": "teams_unknown"})
            raise
        except TeamsNotificationError as exc:
            self._checkpoint_result(job_id, {"phase": "teams_rejected", "http_status": exc.http_status,
                                            "retry_not_before": exc.retry_not_before})
            raise
        except GraphJobNoLongerActiveError:
            raise
        except Exception:
            self._checkpoint_result(job_id, {"phase": "teams_unknown"})
            raise TeamsNotificationUnknownResultError("Resultado Teams desconhecido; requer reconciliação.") from None
        # Receipt and completion are committed together by _mark_complete.
        return {**receipt, "phase": "teams_accepted"}

    def _ensure_teams_child(self, connection, notification, parent_job_id, *, owner=None):
        """Use the caller's writer transaction; never open a second writer."""
        if not connection.in_transaction:
            raise RuntimeError("A intenção Teams exige uma transação ativa.")
        if not notification:
            return None
        child_id = f"teams:{parent_job_id}"
        payload = {"notification": dict(notification)}
        normalized_owner = normalize_job_owner(owner)
        if normalized_owner is not None:
            payload["_owner"] = normalized_owner
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        now = time.time()
        connection.execute("INSERT INTO graph_sync_jobs "
            "(id,kind,payload_json,status,attempts,next_attempt_at,max_attempts,created_at,updated_at) "
            "VALUES (?,'notify_teams',?,'pending',0,?,?,?,?) ON CONFLICT(id) DO NOTHING",
            (child_id, canonical, now, self.teams_job_max_attempts, now, now))
        child = connection.execute("SELECT kind,payload_json,result_json,status FROM graph_sync_jobs WHERE id=?",
                                   (child_id,)).fetchone()
        try:
            stored = json.dumps(json.loads(child["payload_json"]), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (ValueError, TypeError):
            stored = None
        if child["kind"] != "notify_teams" or stored != canonical:
            # Preserve the conflicting payload/receipt for diagnosis. A queued
            # divergent intent must not become an unrelated external effect.
            if child["status"] != "complete":
                result = self._json_object(child["result_json"])
                if not result.get("teams_child_conflict"):
                    result["previous_result_json"] = child["result_json"]
                result.update(invalid_state=True, teams_child_conflict=True)
                connection.execute("UPDATE graph_sync_jobs SET status='failed',retryable=0,result_json=?,"
                    "last_error=?,updated_at=? WHERE id=?", (json.dumps(result, ensure_ascii=False),
                    "Intenção Teams divergente; requer reconciliação.", now, child_id))
            return {"id": child_id, "status": "conflict", "code": "teams_child_conflict"}
        return {"id": child_id, "status": child["status"]}

    def _attach_teams_child(self, connection, parent_id, payload, result):
        child = self._ensure_teams_child(connection, payload.get("teams"), parent_id, owner=payload.get("_owner"))
        result["teams_job"] = child
        if child and child.get("code") == "teams_child_conflict":
            result["teams_child_conflict"] = True
        else:
            result.pop("teams_child_conflict", None)

    def _checkpoint_result(self, job_id: str, partial_result: dict[str, Any]) -> None:
        now = time.time()
        self._assert_job_running(job_id)
        job = self._attempt_context.job
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT result_json,payload_json FROM graph_sync_jobs WHERE id = ? AND status='running' AND attempt_token=?",
                (job_id, job["attempt_token"]),
            ).fetchone()
            if row is None:
                raise GraphJobNoLongerActiveError("A tentativa do worker já não está ativa.")
            current = json.loads(row["result_json"]) if row and row["result_json"] else {}
            current.update(partial_result)
            if (current.get("mail") or {}).get("accepted") is True:
                self._attach_teams_child(connection, job_id, self._json_object(row["payload_json"]), current)
            connection.execute(
                "UPDATE graph_sync_jobs SET result_json = ?, updated_at = ? WHERE id = ? AND status='running' AND attempt_token=?",
                (json.dumps(current, ensure_ascii=False), now, job_id, job["attempt_token"]),
            )
            if (current.get("mail") or {}).get("accepted") is True:
                # The receipt and retirement commit together. Ambiguous sends
                # retain the exact bytes for reconciliation, never automatic replay.
                connection.execute("UPDATE graph_mail_messages SET payload=NULL, retired_at=? "
                                   "WHERE job_id=? AND retired_at IS NULL", (now, job_id))

    def _mark_complete(self, job: dict[str, Any], result: dict[str, Any]) -> None:
        now = time.time()
        job_id = str(job["id"])
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT result_json,payload_json,kind FROM graph_sync_jobs "
                "WHERE id=? AND status='running' AND attempt_token=?", (job_id, job.get("attempt_token", ""))).fetchone()
            if row is None:
                raise GraphJobNoLongerActiveError("A tentativa do worker já não está ativa.")
            merged = self._json_object(row["result_json"])
            merged.update(result)
            if row["kind"] in {"archive_and_remove", "archive_and_mail_local"}:
                self._attach_teams_child(connection, job_id, self._json_object(row["payload_json"]), merged)
            cursor = connection.execute(
                """
                UPDATE graph_sync_jobs
                SET status = 'complete', result_json = ?, last_error = NULL, updated_at = ?
                WHERE id = ? AND status='running' AND attempt_token=?
                """,
                (json.dumps(merged, ensure_ascii=False), now, job_id, job.get("attempt_token", "")),
            )
            if not cursor.rowcount:
                raise GraphJobNoLongerActiveError("A tentativa do worker já não está ativa.")
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
            delay = max(delay, float(retry_after))
            if job["kind"] != "notify_teams":
                delay = min(300.0, delay)
        now = time.time()
        max_attempts = job.get("max_attempts")
        below_limit = max_attempts is None or attempts < int(max_attempts)
        retryable = bool(getattr(error, "retryable", True)) and below_limit
        latest = self.status(str(job["id"]))
        if latest and (latest["reconciliation_required"] or (latest.get("result") or {}).get("invalid_state")):
            retryable = False
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE graph_sync_jobs
                SET status = 'failed', attempts = ?, next_attempt_at = ?, retryable = ?,
                    last_error = ?, updated_at = ?
                WHERE id = ? AND status='running' AND attempt_token=?
                """,
                (
                    attempts,
                    now + delay,
                    1 if retryable else 0,
                    str(error)[:2000],
                    now,
                    job["id"],
                    job.get("attempt_token", ""),
                ),
            )
            if not cursor.rowcount:
                raise GraphJobNoLongerActiveError("A tentativa do worker já não está ativa.")
        return retryable

    def _record_job_failure(self, job: dict[str, Any], error: BaseException) -> None:
        if isinstance(error, GraphJobNoLongerActiveError):
            LOGGER.warning("Tentativa antiga do worker ignorada; estado atual preservado.")
            return
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
                **safe_provider_failure(error),
                exc_info=(type(error), error, error.__traceback__),
            )
        except GraphJobNoLongerActiveError:
            LOGGER.warning("Tentativa antiga do worker ignorada; estado atual preservado.")
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
                    WHERE id = ? AND status = 'running' AND attempt_token=?
                    """,
                    (now, str(message)[:2000], now, job["id"], job.get("attempt_token", "")),
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
            payload = self._json_object(row["payload_json"])
            if row["kind"] == "remove_active":
                result = self._json_object(row["result_json"])
                if result.get("cancellation_started") is not True and result.get("cancellation_confirmed") is not True:
                    # No remote intent was recorded: normal attempt fencing can
                    # safely reclaim this job; _execute still demands its proof.
                    continue
                confirmed = result.get("cancellation_confirmed") is True
                connection.execute(
                    "UPDATE graph_sync_jobs SET status=?,retryable=0,next_attempt_at=?,last_error=?,updated_at=? "
                    "WHERE id=? AND status='running'",
                    ("complete" if confirmed else "failed", now, None if confirmed else
                     "Cancelamento interrompido; confirmar destino e identidade antes de recuperar.", now, row["id"]))
                continue
            if row["kind"] == "notify_teams":
                result = self._json_object(row["result_json"])
                accepted = result.get("accepted") is True
                uncertain = delivery_uncertain("notify_teams", result)
                if uncertain:
                    result["phase"] = "teams_unknown"
                connection.execute(
                    "UPDATE graph_sync_jobs SET status=?,retryable=0,next_attempt_at=?,"
                    "last_error=?,updated_at=?,result_json=? WHERE id=? AND status='running'",
                    ("complete" if accepted else "failed", now, None if accepted else
                     ("Resultado Teams desconhecido; requer reconciliação." if uncertain else
                      "Notificação interrompida antes do pedido; requer revisão manual."),
                     now, json.dumps(result, ensure_ascii=False), row["id"]))
                continue
            has_mail = row["kind"] == "archive_and_mail_local" or bool(payload.get("mail"))
            if not has_mail:
                continue
            result = self._json_object(row["result_json"])
            if dict(result.get("mail") or {}).get("accepted"):
                self._attach_teams_child(connection, row["id"], payload, result)
                cursor = connection.execute(
                    """
                    UPDATE graph_sync_jobs
                    SET status = 'complete', retryable = 0, last_error = NULL,
                        updated_at = ?, result_json = ?
                    WHERE id = ? AND status = 'running'
                    """,
                    (now, json.dumps(result, ensure_ascii=False), row["id"]),
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
        replace_local(temporary, target)

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
            copy_confirmations(original.parent, staged.parent)
            refresh_own_removal_versions(staged.parent)

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
            replace_local(temporary, destination)

    def _publish_locked_metadata(self, staged: Path, original: Path, *, clear_dirty: bool = True) -> None:
        self._copy_newer_metadata(staged.parent, original.parent)
        copy_confirmations(staged.parent, original.parent)
        refresh_own_removal_versions(original.parent)
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

    def _quarantine_invalid_jobs(self, connection: sqlite3.Connection) -> None:
        """Keep malformed records for diagnosis without executing or retrying them."""
        rows = connection.execute("SELECT * FROM graph_sync_jobs WHERE status != 'complete'").fetchall()
        for row in rows:
            existing = self._json_object(row["result_json"])
            if existing.get("invalid_state"):
                connection.execute("UPDATE graph_sync_jobs SET status='failed', retryable=0 WHERE id=?",
                                   (row["id"],))
                continue
            try:
                payload = json.loads(row["payload_json"])
                result = json.loads(row["result_json"]) if row["result_json"] is not None else {}
                if not isinstance(payload, dict) or not isinstance(result, dict):
                    raise ValueError("Expected objects")
                for key in ("mail", "notification", "teams", "_commit_guard"):
                    if payload.get(key) is not None and not isinstance(payload[key], dict):
                        raise ValueError("Invalid payload field")
                if result.get("mail") is not None and not isinstance(result["mail"], dict):
                    raise ValueError("Invalid mail receipt")
                receipt = result.get("mail") or {}
                if "accepted" in receipt and not isinstance(receipt["accepted"], bool):
                    raise ValueError("Invalid acceptance")
                if row["kind"] == "notify_teams" and "accepted" in result and not isinstance(result["accepted"], bool):
                    raise ValueError("Invalid Teams acceptance")
                teams_retry_deadline(row["kind"], result)
                if "phase" in result and not isinstance(result["phase"], str):
                    raise ValueError("Invalid phase")
            except (TypeError, ValueError):
                # Leave payload_json intact and retain result_json verbatim.
                # Neither a malformed receipt nor a missing payload permits effects.
                diagnostic = {"invalid_state": True, "previous_result_json": row["result_json"]}
                connection.execute(
                    "UPDATE graph_sync_jobs SET status='failed', retryable=0, result_json=?, "
                    "last_error=?, updated_at=? WHERE id=?",
                    (json.dumps(diagnostic), "Dados do trabalho inválidos; requer revisão antes de repetir.",
                     time.time(), row["id"]),
                )

    @staticmethod
    def _serialize(row: sqlite3.Row) -> dict[str, Any]:
        status = str(row["status"])
        result = GraphSyncQueue._json_object(row["result_json"])
        uncertain = delivery_uncertain(str(row["kind"]), result)
        will_retry = status == "failed" and bool(row["retryable"]) and not uncertain and not result.get("invalid_state")
        return {
            "id": str(row["id"]),
            "attempt_token": str(row["attempt_token"]),
            "kind": str(row["kind"]),
            "payload": GraphSyncQueue._json_object(row["payload_json"]),
            "status": status,
            "will_retry": will_retry,
            "reconciliation_required": uncertain,
            "attempts": int(row["attempts"]),
            "max_attempts": (
                int(row["max_attempts"]) if row["max_attempts"] is not None else None
            ),
            "next_attempt_at": float(row["next_attempt_at"]),
            "last_error": row["last_error"],
            "result": result if row["result_json"] else None,
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
        }
