"""Explicit runtime mode and guards at external delivery boundaries."""

import os


def runtime_mode() -> str:
    mode = os.environ.get("FS_ENVIRONMENT", "development").strip().lower()
    if mode not in {"development", "test", "production"}:
        raise ValueError("FS_ENVIRONMENT deve ser development, test ou production.")
    return mode


def require_live_delivery(enabled: bool) -> None:
    if runtime_mode() != "production" or not enabled:
        raise ValueError(
            "Transporte externo bloqueado. O envio real requer modo production "
            "e a integração explicitamente ativada."
        )
