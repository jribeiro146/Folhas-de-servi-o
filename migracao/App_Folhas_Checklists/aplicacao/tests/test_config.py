import json
import os
from pathlib import Path
import subprocess
import sys

import src.config as config


def test_migration_default_never_reuses_a_neighbouring_operational_excel(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    exe_dir = project_root / "dist" / "FolhasServico"
    (project_root / "Excel").mkdir(parents=True)
    exe_dir.mkdir(parents=True)

    monkeypatch.setattr(config.sys, "frozen", True, raising=False)
    monkeypatch.setattr(config.sys, "executable", str(exe_dir / "FolhasServico.exe"))
    local_data = tmp_path / "isolated-local-app-data"
    monkeypatch.setenv("LOCALAPPDATA", str(local_data))
    monkeypatch.chdir(exe_dir)

    detected = config._detect_default_base_path()

    assert detected.is_relative_to(local_data)
    assert detected != project_root
    monkeypatch.chdir(project_root)
    assert config._detect_default_base_path() == detected
    assert not detected.exists()  # Looking up the default must not create storage.


def test_all_microsoft_services_ignore_legacy_secrets_and_use_graph_identity():
    project_root = Path(__file__).resolve().parents[1]
    missing_env_file = project_root / ".credential-unification-test-env-does-not-exist"
    assert not missing_env_file.exists()
    environment = os.environ.copy()
    environment.update({
        "FS_ENV_FILE": str(missing_env_file),
        "GRAPH_TENANT_ID": "canonical-tenant",
        "GRAPH_CLIENT_ID": "canonical-client",
        "GRAPH_CLIENT_SECRET": "canonical-secret",
        "GRAPH_MAIL_TENANT_ID": "stale-mail-tenant",
        "GRAPH_MAIL_CLIENT_ID": "stale-mail-client",
        "GRAPH_MAIL_CLIENT_SECRET": "expired-mail-secret",
        "MICROSOFT_AUTH_TENANT_ID": "stale-login-tenant",
        "MICROSOFT_AUTH_CLIENT_ID": "stale-login-client",
        "MICROSOFT_AUTH_CLIENT_SECRET": "expired-login-secret",
        "FS_MAIL_SENDER": "service@sensorpoint.pt",
    })
    probe = """
import json
from src.services.graph_mail_service import GraphMailConfig
from src.services.graph_storage_service import GraphConfig
from src.services.microsoft_auth_service import MicrosoftAuthService

storage = GraphConfig.from_env()
mail = GraphMailConfig.from_env()
login = MicrosoftAuthService()
print(json.dumps({
    "storage": [storage.tenant_id, storage.client_id, storage.client_secret],
    "mail": [mail.tenant_id, mail.client_id, mail.client_secret],
    "login": [login.tenant_id, login.client_id, login.client_secret],
}))
"""

    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=project_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    identity = ["canonical-tenant", "canonical-client", "canonical-secret"]
    assert json.loads(completed.stdout) == {
        "storage": identity,
        "mail": identity,
        "login": identity,
    }


def test_env_file_uses_last_duplicate_without_overriding_process_environment(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    env_file = tmp_path / ".env"
    env_file.write_text(
        "GRAPH_CLIENT_SECRET=stale-secret\n"
        "GRAPH_CLIENT_SECRET=rotated-secret\n",
        encoding="utf-8",
    )
    probe = "from src.config import GRAPH_CLIENT_SECRET; print(GRAPH_CLIENT_SECRET)"

    environment = os.environ.copy()
    environment.pop("GRAPH_CLIENT_SECRET", None)
    environment["FS_ENV_FILE"] = str(env_file)
    from_file = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=project_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert from_file.stdout.strip() == "rotated-secret"

    environment["GRAPH_CLIENT_SECRET"] = "process-secret"
    from_process = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=project_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert from_process.stdout.strip() == "process-secret"
