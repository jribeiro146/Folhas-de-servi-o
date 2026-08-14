from __future__ import annotations

import gzip
import json
import logging
import multiprocessing
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.logging_config import (
    LoggingSettings,
    bind_request_context,
    configure_logging,
    log_event,
    reset_request_context,
    shutdown_logging,
)


LOG_FILE_NAME = "folhas-servico.jsonl"


def _settings(directory: Path, **overrides) -> LoggingSettings:
    values = {
        "enabled": True,
        "level": "INFO",
        "directory": directory,
        "file_name": LOG_FILE_NAME,
        "max_bytes": 64 * 1024,
        "backup_count": 6,
        "stderr": False,
    }
    values.update(overrides)
    return LoggingSettings(**values)


def _read_records(directory: Path) -> list[dict]:
    paths = [directory / LOG_FILE_NAME, *sorted(directory.glob(f"{LOG_FILE_NAME}.*.gz"))]
    records: list[dict] = []
    for path in paths:
        if not path.exists():
            continue
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as stream:
            records.extend(json.loads(line) for line in stream if line.strip())
    return records


def _write_from_process(directory: str, worker: str, count: int) -> None:
    target = Path(directory)
    configure_logging(worker, settings=_settings(target), force=True)
    logger = logging.getLogger(f"test.{worker}")
    for index in range(count):
        log_event(
            logger,
            logging.INFO,
            "x" * 1200,
            event="multiprocess_probe",
            worker=worker,
            index=index,
        )
    shutdown_logging()


@pytest.fixture(autouse=True)
def clean_application_logging():
    root = logging.getLogger()
    previous_level = root.level
    shutdown_logging()
    try:
        yield
    finally:
        shutdown_logging()
        root.setLevel(previous_level)


def test_json_logging_redacts_secrets_and_carries_request_context(tmp_path):
    configure_logging("test-web", settings=_settings(tmp_path), force=True)
    logger = logging.getLogger("test.application")
    tokens = bind_request_context(
        "request-123",
        "POST",
        "/api/file/<name>/send",
    )
    try:
        try:
            raise RuntimeError(
                "client_secret=exception-secret "
                "https://example.test/callback?sig=query-secret"
            )
        except RuntimeError:
            log_event(
                logger,
                logging.ERROR,
                "Bearer message-secret access_token=another-secret "
                "data:image/png;base64,AAAA",
                event="redaction_probe",
                authorization="Basic field-secret",
                api_key="api-secret",
                nested={"password": "nested-secret", "safe": "preserved"},
                safe_field="visible",
                exc_info=True,
            )
    finally:
        reset_request_context(tokens)
        shutdown_logging()

    raw_log = (tmp_path / LOG_FILE_NAME).read_text(encoding="utf-8")
    for secret in (
        "message-secret",
        "another-secret",
        "exception-secret",
        "query-secret",
        "field-secret",
        "api-secret",
        "nested-secret",
        "AAAA",
    ):
        assert secret not in raw_log

    record = next(
        item for item in _read_records(tmp_path) if item.get("event") == "redaction_probe"
    )
    assert record["schema_version"] == 1
    assert record["timestamp"].endswith("Z")
    assert record["component"] == "test-web"
    assert record["request_id"] == "request-123"
    assert record["http_method"] == "POST"
    assert record["http_route"] == "/api/file/<name>/send"
    assert record["authorization"] == "<redacted>"
    assert record["api_key"] == "<redacted>"
    assert record["nested"] == {"password": "<redacted>", "safe": "preserved"}
    assert record["safe_field"] == "visible"
    assert "<redacted-query>" in record["exception"]


def test_request_log_uses_route_template_and_returns_request_id(tmp_path, monkeypatch):
    import src.web.application as application_module

    monkeypatch.setattr(application_module, "ACTIVE_AUTH_PROVIDER", "none")
    monkeypatch.setattr(application_module, "AUTH_ENABLED", False)
    monkeypatch.setattr(application_module, "MAIL_ENABLED", False)
    monkeypatch.setattr(application_module, "TEAMS_NOTIFICATIONS_ENABLED", False)
    monkeypatch.setattr(application_module, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(application_module, "LOG_REQUESTS", True)

    configure_logging("test-web", settings=_settings(tmp_path), force=True)
    app = application_module.create_app(
        file_service=SimpleNamespace(directory=tmp_path),
        archive_service=object(),
        editing_state_service=object(),
        work_folder_service=object(),
    )
    response = app.test_client().post(
        "/api/file/Cliente%20Confidencial/send?access_token=query-secret",
        json={"probe": True},
        headers={"X-Request-ID": "request-from-proxy"},
    )
    log_event(
        logging.getLogger("test.application"),
        logging.INFO,
        "Evento fora do pedido.",
        event="after_request_probe",
    )
    shutdown_logging()

    assert response.status_code == 409
    assert response.headers["X-Request-ID"] == "request-from-proxy"
    raw_log = (tmp_path / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "Cliente Confidencial" not in raw_log
    assert "query-secret" not in raw_log

    records = _read_records(tmp_path)
    request_record = next(item for item in records if item.get("event") == "http_request_completed")
    assert request_record["request_id"] == "request-from-proxy"
    assert request_record["http_route"] == "/api/file/<name>/send"
    assert request_record["status_code"] == 409
    assert request_record["endpoint"] == "save_and_send"
    after_record = next(item for item in records if item.get("event") == "after_request_probe")
    assert "request_id" not in after_record


def test_rotation_is_valid_json_and_safe_between_processes(tmp_path):
    process_count = 2
    records_per_process = 120
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(
            target=_write_from_process,
            args=(str(tmp_path), f"worker-{index}", records_per_process),
        )
        for index in range(process_count)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=30)

    assert [process.exitcode for process in processes] == [0] * process_count
    assert list(tmp_path.glob(f"{LOG_FILE_NAME}.*.gz"))
    probe_records = [
        item for item in _read_records(tmp_path) if item.get("event") == "multiprocess_probe"
    ]
    assert len(probe_records) == process_count * records_per_process
    assert {
        (item["worker"], item["index"])
        for item in probe_records
    } == {
        (f"worker-{worker}", index)
        for worker in range(process_count)
        for index in range(records_per_process)
    }


@pytest.mark.parametrize("file_name", ["../application.jsonl", "nested/application.jsonl"])
def test_log_file_name_must_not_contain_a_path(tmp_path, file_name):
    with pytest.raises(ValueError, match="nome de ficheiro"):
        configure_logging(
            "test",
            settings=_settings(tmp_path, file_name=file_name),
            force=True,
        )


def test_log_directory_must_be_absolute():
    with pytest.raises(ValueError, match="caminho absoluto"):
        configure_logging(
            "test",
            settings=_settings(Path("relative-logs")),
            force=True,
        )
