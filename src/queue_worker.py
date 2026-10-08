"""Worker independente para a fila persistente de Graph e e-mail."""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from src.config import (
    APP_DATA_DIR,
    GRAPH_CACHE_DIR,
    LOG_DIR,
    EXCEL_ACTIVAS_DIR,
    EXCEL_ARQUIVADAS_DIR,
    EXCEL_CANCELADAS_DIR,
    MAIL_ENABLED,
    STORAGE_BACKEND,
    TEAMS_NOTIFICATIONS_ENABLED,
)
from src.logging_config import configure_logging, log_event
from src.services.graph_mail_service import GraphMailService
from src.services.graph_storage_service import GraphStorageService
from src.services.graph_sync_queue import GraphSyncQueue
from src.services.local_pdf_service import LocalPdfService
from src.services.teams_notification_service import TeamsNotificationService
from src.services.runtime_admission import resolve_runtime, operation_for, admit_contract
from src.services.runtime_contract import ContractBusy


LOGGER = logging.getLogger(__name__)


def build_queue(*, release_root: Path | None = None, runtime_contract=None) -> GraphSyncQueue:
    """Cria a mesma fila da webapp sem arrancar threads dentro do WSGI."""
    release_info, contract = resolve_runtime(
        APP_DATA_DIR, release_root=release_root, runtime_contract=runtime_contract,
        storage_paths=(GRAPH_CACHE_DIR, LOG_DIR, EXCEL_ACTIVAS_DIR, EXCEL_ARQUIVADAS_DIR, EXCEL_CANCELADAS_DIR),
    )
    with admit_contract(contract):
        return _build_admitted_queue(release_info, contract)


def _build_admitted_queue(release_info, contract):
    graph_service = GraphStorageService() if STORAGE_BACKEND == "graph" else None
    mail_service = GraphMailService() if MAIL_ENABLED else None
    local_pdf_service = (
        LocalPdfService() if mail_service is not None and graph_service is None else None
    )
    teams_notification_service = (
        TeamsNotificationService()
        if TEAMS_NOTIFICATIONS_ENABLED and mail_service is not None
        else None
    )
    queue = GraphSyncQueue(
        graph_service,
        APP_DATA_DIR / "graph-sync.sqlite3",
        mail_service=mail_service,
        local_pdf_service=local_pdf_service,
        teams_notification_service=teams_notification_service,
        auto_start=False,
        runtime_contract=contract,
    )
    queue.release_info = release_info
    return queue


def process_once(queue: GraphSyncQueue | None = None) -> dict[str, object]:
    """Executa os trabalhos atualmente prontos e devolve um resumo observavel."""
    active_queue = queue or build_queue()
    with operation_for(active_queue):
        before = active_queue.summary()
        acquired = active_queue.run_until_idle()
        return {
            "release": getattr(active_queue, "release_info", None),
            "worker_acquired": acquired,
            "before": before,
            "after": active_queue.summary(),
        }


def main(argv: list[str] | None = None, *, release_root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Mantem o worker ativo; sem esta opcao processa uma vez e termina.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Segundos entre verificacoes no modo --watch (predefinido: 5).",
    )
    args = parser.parse_args(argv)

    try:
        queue = None
        while True:
            try:
                if queue is None:
                    candidate = build_queue(release_root=release_root)
                    with operation_for(candidate):
                        configure_logging("worker")
                    queue = candidate
                    log_event(
                        LOGGER, logging.INFO, "Worker da fila iniciado.",
                        event="queue_worker_started",
                        source_revision=queue.release_info["revision"],
                        source_sha256=queue.release_info["source_sha256"],
                        watch=args.watch, interval_seconds=max(args.interval, 1.0),
                    )
                result = process_once(queue)
            except ContractBusy:
                log_event(LOGGER, logging.WARNING, "Ciclo adiado: contrato temporariamente ocupado.",
                          event="queue_worker_admission_busy")
                if not args.watch:
                    return 1
                time.sleep(max(args.interval, 1.0))
                continue
            has_activity = (
                result["before"] != result["after"]
                or any(
                    int(result["before"].get(status, 0)) > 0
                    for status in ("pending", "running")
                )
            )
            log_event(
                LOGGER,
                logging.INFO if has_activity or not args.watch else logging.DEBUG,
                "Ciclo do worker concluído.",
                event="queue_worker_cycle_completed",
                worker_acquired=result["worker_acquired"],
                before=result["before"],
                after=result["after"],
            )
            if not args.watch:
                return 0
            time.sleep(max(args.interval, 1.0))
    except Exception:
        log_event(
            LOGGER,
            logging.CRITICAL,
            "O worker terminou com um erro não tratado.",
            event="queue_worker_failed",
            exc_info=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
