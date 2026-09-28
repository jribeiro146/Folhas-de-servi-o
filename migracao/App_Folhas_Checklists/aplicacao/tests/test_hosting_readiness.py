"""Regressões de configuração e recursos públicos, com identidade fictícia."""

import logging
from types import SimpleNamespace

import pytest

import src.config as config
from src.services.runtime_safety import require_live_delivery, runtime_mode
from src.services.editing_state_service import EditingStateService
from src.services.file_service import FileService
import src.web.application as application


@pytest.mark.parametrize("auth,storage", [("microsoft", "local"), ("none", "graph")])
def test_missing_environment_warns_without_enabling_production(monkeypatch, caplog, auth, storage):
    monkeypatch.delenv("FS_ENVIRONMENT", raising=False)
    monkeypatch.setattr(config, "AUTH_PROVIDER", auth)
    monkeypatch.setattr(config, "STORAGE_BACKEND", storage)
    with caplog.at_level(logging.WARNING, logger="src.config"):
        config._warn_implicit_environment()
    assert "FS_ENVIRONMENT ausente" in caplog.text
    assert runtime_mode() == "development"
    assert "FS_ENVIRONMENT" not in config.os.environ
    with pytest.raises(ValueError, match="Transporte externo bloqueado"):
        require_live_delivery(True)


@pytest.mark.parametrize("environment", ["test", "development", "production"])
def test_explicit_environment_does_not_emit_missing_environment_warning(monkeypatch, caplog, environment):
    monkeypatch.setenv("FS_ENVIRONMENT", environment)
    monkeypatch.setattr(config, "AUTH_PROVIDER", "microsoft")
    config._warn_implicit_environment()
    assert "FS_ENVIRONMENT ausente" not in caplog.text


@pytest.mark.parametrize("signed_in", [False, True])
def test_manifest_and_worker_remain_public_without_maintenance_demo(monkeypatch, tmp_path, signed_in):
    monkeypatch.setenv("FS_TEST_SYNTHETIC", "false")
    monkeypatch.setattr(application, "ACTIVE_AUTH_PROVIDER", "microsoft")
    monkeypatch.setattr(application, "AUTH_ENABLED", True)
    fake_login = SimpleNamespace(user_from_session=lambda payload: payload)
    app = application.create_app(
        file_service=FileService(tmp_path / "active"),
        editing_state_service=EditingStateService(tmp_path / "editing"),
        microsoft_auth_service=fake_login,
    )
    assert app.config["MAINTENANCE_DEMO"] is False
    client = app.test_client()
    if signed_in:
        with client.session_transaction() as session:
            session["microsoft_user"] = {"id": "synthetic-user", "name": "Técnico fictício"}
    response = client.get("/manifest.webmanifest")
    assert response.status_code == 200
    assert response.mimetype == "application/manifest+json"
    assert response.get_json()["start_url"] == "/"
    assert response.get_json()["icons"]
    response = client.get("/service-worker.js")
    assert response.status_code == 200
    assert "no-store" in response.headers["Cache-Control"]


@pytest.mark.parametrize("environment,secure", [("development", False), ("test", False), ("production", True)])
def test_secure_cookie_still_requires_explicit_production(monkeypatch, tmp_path, environment, secure):
    monkeypatch.setenv("FS_ENVIRONMENT", environment)
    app = application.create_app(
        file_service=FileService(tmp_path / "active"),
        editing_state_service=EditingStateService(tmp_path / "editing"),
    )
    assert app.config["SESSION_COOKIE_SECURE"] is secure
