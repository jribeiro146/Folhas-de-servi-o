"""Worker independente para a fila persistente de Graph e e-mail."""

from __future__ import annotations

import argparse
import json
import time

from src.config import (
    APP_DATA_DIR,
    MAIL_ENABLED,
    STORAGE_BACKEND,
    TEAMS_NOTIFICATIONS_ENABLED,
)
from src.services.graph_mail_service import GraphMailService
from src.services.graph_storage_service import GraphStorageService
from src.services.graph_sync_queue import GraphSyncQueue
from src.services.local_pdf_service import LocalPdfService
from src.services.teams_notification_service import TeamsNotificationService


def build_queue() -> GraphSyncQueue:
    """Cria a mesma fila da webapp sem arrancar threads dentro do WSGI."""
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
    return GraphSyncQueue(
        graph_service,
        APP_DATA_DIR / "graph-sync.sqlite3",
        mail_service=mail_service,
        local_pdf_service=local_pdf_service,
        teams_notification_service=teams_notification_service,
        auto_start=False,
    )


def process_once(queue: GraphSyncQueue | None = None) -> dict[str, object]:
    """Executa os trabalhos atualmente prontos e devolve um resumo observavel."""
    active_queue = queue or build_queue()
    before = active_queue.summary()
    acquired = active_queue.run_until_idle()
    return {
        "worker_acquired": acquired,
        "before": before,
        "after": active_queue.summary(),
    }


def main() -> int:
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
    args = parser.parse_args()

    queue = build_queue()
    while True:
        print(json.dumps(process_once(queue), ensure_ascii=False), flush=True)
        if not args.watch:
            return 0
        time.sleep(max(args.interval, 1.0))


if __name__ == "__main__":
    raise SystemExit(main())
