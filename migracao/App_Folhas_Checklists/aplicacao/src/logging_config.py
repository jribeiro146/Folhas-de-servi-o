"""Logging operacional estruturado, rotativo e seguro entre processos."""

from __future__ import annotations

import contextvars
import datetime as dt
import json
import logging
import os
import re
import socket
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.config import (
    LOG_BACKUP_COUNT,
    LOG_DIR,
    LOG_ENABLED,
    LOG_FILE_NAME,
    LOG_LEVEL,
    LOG_MAX_BYTES,
    LOG_STDERR,
)


LOGGER_NAME = "folhas_servico"
_HANDLER_MARKER = "_folhas_servico_handler"
_MAX_LOG_TEXT = 32_768
_MAX_FIELD_TEXT = 4_096
_VALID_LEVELS = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}
_RESERVED_FIELDS = {
    "schema_version",
    "timestamp",
    "level",
    "component",
    "host",
    "logger",
    "message",
    "pid",
    "thread",
    "event",
    "request_id",
    "http_method",
    "http_route",
    "exception",
    "stack",
}
_SENSITIVE_KEY = re.compile(
    r"(?:authorization|cookie|password|passwd|secret|token|signature|webhook|api[_-]?key)",
    re.IGNORECASE,
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(authorization|client_secret|access_token|refresh_token|id_token|password|passwd|"
    r"cookie|api[_-]?key|sig)"
    r"(\s*[:=]\s*)([^\s,;\"'}]+|\"[^\"]*\"|'[^']*')"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_DATA_URL = re.compile(r"data:[^;\s]+;base64,[A-Za-z0-9+/=]+", re.IGNORECASE)
_URL_QUERY = re.compile(r"(https?://[^\s?#\"']+)\?[^\s\"']+", re.IGNORECASE)
_REQUEST_ID = contextvars.ContextVar("fs_log_request_id", default="")
_HTTP_METHOD = contextvars.ContextVar("fs_log_http_method", default="")
_HTTP_ROUTE = contextvars.ContextVar("fs_log_http_route", default="")
_HOSTNAME = socket.gethostname()
_component = "application"
_configured_pid: int | None = None


@dataclass(frozen=True)
class LoggingSettings:
    """Configuracao resolvida para um processo da aplicacao."""

    enabled: bool = LOG_ENABLED
    level: str = LOG_LEVEL
    directory: Path = LOG_DIR
    file_name: str = LOG_FILE_NAME
    max_bytes: int = LOG_MAX_BYTES
    backup_count: int = LOG_BACKUP_COUNT
    stderr: bool = LOG_STDERR

    @property
    def file_path(self) -> Path:
        return self.directory / self.file_name


@dataclass(frozen=True)
class LoggingRuntime:
    """Resultado observavel da inicializacao do logging."""

    enabled: bool
    file_path: Path | None
    level: str
    component: str


def _redact_text(value: object, *, limit: int = _MAX_LOG_TEXT) -> str:
    text = str(value)
    text = _BEARER.sub("Bearer <redacted>", text)
    text = _SECRET_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}{match.group(2)}<redacted>",
        text,
    )
    text = _DATA_URL.sub("<redacted-data-url>", text)
    text = _URL_QUERY.sub(r"\1?<redacted-query>", text)
    if len(text) > limit:
        return f"{text[:limit]}…<truncated>"
    return text


def _safe_value(key: str, value: Any) -> Any:
    if _SENSITIVE_KEY.search(key):
        return "<redacted>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Path):
        return _redact_text(value, limit=_MAX_FIELD_TEXT)
    if isinstance(value, dict):
        return {
            str(child_key): _safe_value(str(child_key), child_value)
            for child_key, child_value in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(key, item) for item in value]
    return _redact_text(value, limit=_MAX_FIELD_TEXT)


class JsonLogFormatter(logging.Formatter):
    """Uma linha JSON por evento, com campos permitidos e segredos removidos."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = dt.datetime.fromtimestamp(
            record.created,
            tz=dt.timezone.utc,
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        payload: dict[str, Any] = {
            "schema_version": 1,
            "timestamp": timestamp,
            "level": record.levelname,
            "component": _component,
            "host": _HOSTNAME,
            "logger": record.name,
            "message": _redact_text(record.getMessage()),
            "pid": record.process,
            "thread": record.threadName,
        }
        event = str(getattr(record, "event", "") or "").strip()
        if event:
            payload["event"] = event

        request_id = _REQUEST_ID.get()
        method = _HTTP_METHOD.get()
        route = _HTTP_ROUTE.get()
        if request_id:
            payload["request_id"] = request_id
        if method:
            payload["http_method"] = method
        if route:
            payload["http_route"] = route

        fields = getattr(record, "event_fields", None)
        if isinstance(fields, dict):
            for key, value in fields.items():
                normalized_key = str(key)
                if normalized_key not in _RESERVED_FIELDS:
                    payload[normalized_key] = _safe_value(normalized_key, value)

        if record.exc_info:
            payload["exception"] = _redact_text(self.formatException(record.exc_info))
        if record.stack_info:
            payload["stack"] = _redact_text(self.formatStack(record.stack_info))
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _validated_settings(settings: LoggingSettings) -> tuple[int, Path]:
    level_name = settings.level.strip().upper()
    if level_name not in _VALID_LEVELS:
        raise ValueError(
            f"FS_LOG_LEVEL inválido: {settings.level!r}. "
            "Use DEBUG, INFO, WARNING, ERROR ou CRITICAL."
        )
    if (
        not settings.file_name
        or "/" in settings.file_name
        or "\\" in settings.file_name
        or Path(settings.file_name).name != settings.file_name
        or settings.file_name in {".", ".."}
    ):
        raise ValueError("FS_LOG_FILE_NAME deve conter apenas um nome de ficheiro.")
    if settings.max_bytes < 64 * 1024:
        raise ValueError("FS_LOG_MAX_BYTES deve ser pelo menos 65536.")
    if settings.backup_count < 1:
        raise ValueError("FS_LOG_BACKUP_COUNT deve ser pelo menos 1.")
    directory = Path(settings.directory)
    if not directory.is_absolute():
        raise ValueError("FS_LOG_DIR deve ser um caminho absoluto em disco local.")
    resolved_directory = directory.resolve()
    if resolved_directory == Path(resolved_directory.anchor):
        raise ValueError("FS_LOG_DIR não pode ser a raiz do disco.")
    return _VALID_LEVELS[level_name], resolved_directory


def _remove_application_handlers() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass


def configure_logging(
    component: str,
    *,
    settings: LoggingSettings | None = None,
    force: bool = False,
) -> LoggingRuntime:
    """Configura logging num processo web, worker ou arranque local.

    Cada processo cria o seu proprio handler. Todos escrevem no mesmo ficheiro
    usando um lock multiprocesso durante a escrita e a rotacao.
    """

    global _component, _configured_pid
    resolved = settings or LoggingSettings()
    component_name = str(component or "application").strip() or "application"
    current_pid = os.getpid()
    if (
        not force
        and _configured_pid == current_pid
        and any(
            getattr(handler, _HANDLER_MARKER, False)
            for handler in logging.getLogger().handlers
        )
    ):
        _component = component_name
        return LoggingRuntime(
            enabled=resolved.enabled,
            file_path=resolved.file_path.resolve() if resolved.enabled else None,
            level=resolved.level.upper(),
            component=component_name,
        )

    _remove_application_handlers()
    _component = component_name
    _configured_pid = current_pid
    if not resolved.enabled:
        return LoggingRuntime(False, None, resolved.level.upper(), component_name)

    level, directory = _validated_settings(resolved)
    try:
        from concurrent_log_handler import ConcurrentRotatingFileHandler
    except ImportError as exc:  # pragma: no cover - exercido apenas num deploy incompleto
        raise RuntimeError(
            "Falta a dependência concurrent-log-handler. "
            "Execute: pip install -r requirements-server.txt"
        ) from exc

    directory.mkdir(parents=True, exist_ok=True, mode=0o750)
    if os.name != "nt":
        os.chmod(directory, 0o750)
    file_path = directory / resolved.file_name
    try:
        # A rotacao precisa de criar, renomear e apagar ficheiros no diretorio.
        with tempfile.TemporaryFile(dir=directory):
            pass
        with file_path.open("a", encoding="utf-8", newline=""):
            pass
        if os.name != "nt":
            os.chmod(file_path, 0o640)
    except OSError as exc:
        raise RuntimeError(
            f"Não foi possível preparar o ficheiro de log privado: {file_path}"
        ) from exc

    formatter = JsonLogFormatter()
    file_handler = ConcurrentRotatingFileHandler(
        filename=str(file_path),
        mode="a",
        maxBytes=resolved.max_bytes,
        backupCount=resolved.backup_count,
        encoding="utf-8",
        use_gzip=True,
        chmod=0o640,
        newline="",
        terminator="\n",
    )
    setattr(file_handler, _HANDLER_MARKER, True)
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(file_handler)
    if resolved.stderr:
        stderr_handler = logging.StreamHandler(sys.stderr)
        setattr(stderr_handler, _HANDLER_MARKER, True)
        stderr_handler.setLevel(level)
        stderr_handler.setFormatter(formatter)
        root.addHandler(stderr_handler)

    logging.captureWarnings(True)
    for noisy_logger in ("urllib3", "PIL", "msal"):
        logging.getLogger(noisy_logger).setLevel(max(level, logging.WARNING))

    log_event(
        logging.getLogger(LOGGER_NAME),
        logging.INFO,
        "Logging operacional inicializado.",
        event="logging_configured",
        log_file=file_path,
        max_bytes=resolved.max_bytes,
        backup_count=resolved.backup_count,
        stderr=resolved.stderr,
    )
    return LoggingRuntime(True, file_path, logging.getLevelName(level), component_name)


def shutdown_logging() -> None:
    """Fecha apenas handlers desta aplicacao; usado no encerramento e em testes."""

    global _configured_pid
    _remove_application_handlers()
    _configured_pid = None


def log_event(
    logger: logging.Logger,
    level: int,
    message: str,
    *,
    event: str,
    exc_info: Any = None,
    **fields: Any,
) -> None:
    """Emite um evento estruturado sem permitir campos sobrepor o envelope."""

    logger.log(
        level,
        message,
        extra={"event": event, "event_fields": fields},
        exc_info=exc_info,
    )


def bind_request_context(
    request_id: str,
    method: str,
    route: str,
) -> tuple[contextvars.Token[str], contextvars.Token[str], contextvars.Token[str]]:
    """Associa os campos HTTP ao contexto atual e devolve tokens para reset."""

    return (
        _REQUEST_ID.set(request_id),
        _HTTP_METHOD.set(method),
        _HTTP_ROUTE.set(route),
    )


def reset_request_context(
    tokens: tuple[
        contextvars.Token[str],
        contextvars.Token[str],
        contextvars.Token[str],
    ]
    | None,
) -> None:
    """Remove o contexto HTTP para impedir contaminacao entre pedidos."""

    if not tokens:
        return
    for variable, token in zip(
        (_REQUEST_ID, _HTTP_METHOD, _HTTP_ROUTE),
        tokens,
        strict=True,
    ):
        variable.reset(token)
