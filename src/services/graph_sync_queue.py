"""Persistent transactional outbox for Microsoft Graph operations."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from src.services.graph_mail_service import GraphMailService
from src.services.graph_storage_service import GraphStorageService
from src.services.local_pdf_service import LocalPdfService
from src.services.teams_notification_service import TeamsNotificationService


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
        self._worker_lock = threading.Lock()
        self._worker_running = False
        self._initialize()
        self.start()

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
                            last_error TEXT,
                            result_json TEXT,
                            created_at REAL NOT NULL,
                            updated_at REAL NOT NULL
                        )
                        """
                    )
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).casefold() or time.monotonic() >= deadline:
                    raise
                time.sleep(0.025)

    def enqueue(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        identifier = job_id or str(uuid.uuid4())
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM graph_sync_jobs WHERE id = ?", (identifier,)
            ).fetchone()
            if existing is None:
                prepared_payload = self._stage_payload(identifier, kind, payload)
                connection.execute(
                    """
                    INSERT INTO graph_sync_jobs (
                        id, kind, payload_json, status, attempts,
                        next_attempt_at, created_at, updated_at
                    ) VALUES (?, ?, ?, 'pending', 0, ?, ?, ?)
                    """,
                    (
                        identifier,
                        kind,
                        json.dumps(
                            prepared_payload,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        now,
                        now,
                        now,
                    ),
                )
            connection.commit()
        self.start()
        return self.status(identifier) or {"id": identifier, "status": "pending"}

    def start(self) -> None:
        with self._worker_lock:
            if self._worker_running:
                return
            self._worker_running = True
        threading.Thread(target=self._worker, daemon=True, name="graph-sync-outbox").start()

    def status(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM graph_sync_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._serialize(row) if row else None

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

    def _worker(self) -> None:
        try:
            while True:
                job = self._claim_next()
                if job is None:
                    return
                try:
                    result = self._execute(
                        job["kind"],
                        job["payload"],
                        attempts=job["attempts"],
                        job_id=job["id"],
                        prior_result=job.get("result"),
                    )
                except Exception as exc:
                    self._mark_failed(job, str(exc))
                else:
                    self._mark_complete(job, result)
        finally:
            with self._worker_lock:
                self._worker_running = False
            if self._has_active_job():
                delay = 0.25
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
                    status IN ('pending', 'failed') AND next_attempt_at <= ?
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
            uploaded = self.graph_service.upload_active_bundle(
                Path(payload["draft_path"]),
                fail_if_exists=bool(payload.get("fail_if_exists")) and attempts == 1,
            )
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
                pdf_path, remote_pdf = self.graph_service.export_archive_pdf(archived_path)
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
            pdf_path = self.local_pdf_service.export_archive_pdf(archived_path)
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
        mail_result = self.mail_service.send_prepared(
            mail_payload,
            pdf_path,
            operation_id=job_id,
        )
        self._checkpoint_result(job_id, {"mail": mail_result})
        return mail_result

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
            connection.execute(
                """
                UPDATE graph_sync_jobs
                SET status = 'complete', result_json = ?, last_error = NULL, updated_at = ?
                WHERE id = ?
                """,
                (json.dumps(result, ensure_ascii=False), now, job_id),
            )

        self._cleanup_staging(job["payload"])

    def _mark_failed(self, job: dict[str, Any], error: str) -> None:
        attempts = int(job.get("attempts") or 1)
        delay = min(300, 2 ** min(attempts, 8))
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE graph_sync_jobs
                SET status = 'failed', attempts = ?, next_attempt_at = ?,
                    last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (attempts, now + delay, error[:2000], now, job["id"]),
            )

    def _has_due_jobs(self) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM graph_sync_jobs
                WHERE status = 'pending' OR (status = 'failed' AND next_attempt_at <= ?)
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
                "SELECT MIN(next_attempt_at) AS due_at FROM graph_sync_jobs WHERE status = 'failed'"
            ).fetchone()
        due_at = row["due_at"] if row else None
        if due_at is None:
            return None
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
            and (path.name == ".graph_bundle.json" or path.name.endswith(".graph.json"))
        ]

    def _hydrate_staging_metadata(self, payload: dict[str, Any]) -> None:
        staged = Path(str(payload["draft_path"]))
        original_value = str(payload.get("_original_draft_path") or "")
        if not original_value:
            return
        original = Path(original_value)
        if not original.parent.exists() or not staged.parent.exists():
            return
        for source in self._metadata_files(original.parent):
            destination = staged.parent / source.relative_to(original.parent)
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

    def _publish_staging_metadata(self, payload: dict[str, Any]) -> None:
        staged = Path(str(payload["draft_path"]))
        original_value = str(payload.get("_original_draft_path") or "")
        if not original_value:
            return
        original = Path(original_value)
        if not original.parent.exists() or not staged.parent.exists():
            return
        for source in self._metadata_files(staged.parent):
            destination = original.parent / source.relative_to(staged.parent)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex[:8]}.tmp")
            shutil.copy2(source, temporary)
            os.replace(str(temporary), str(destination))


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
    def _serialize(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "kind": str(row["kind"]),
            "payload": json.loads(row["payload_json"]),
            "status": str(row["status"]),
            "attempts": int(row["attempts"]),
            "next_attempt_at": float(row["next_attempt_at"]),
            "last_error": row["last_error"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
        }
