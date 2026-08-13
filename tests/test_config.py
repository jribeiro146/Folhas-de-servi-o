import json
import os
from pathlib import Path
import subprocess
import sys

import src.config as config


def test_detect_default_base_path_prefers_parent_with_excel(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    exe_dir = project_root / "dist" / "FolhasServico"
    (project_root / "Excel").mkdir(parents=True)
    exe_dir.mkdir(parents=True)

    monkeypatch.setattr(config.sys, "frozen", True, raising=False)
    monkeypatch.setattr(config.sys, "executable", str(exe_dir / "FolhasServico.exe"))
    monkeypatch.chdir(exe_dir)

    detected = config._detect_default_base_path()

    assert detected == project_root


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
