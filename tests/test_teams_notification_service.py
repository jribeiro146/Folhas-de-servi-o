import json
from pathlib import Path

import pytest

import src.services.teams_notification_service as teams_module
from src.services.graph_sync_queue import GraphSyncQueue
from src.services.teams_notification_service import (
    TeamsNotificationConfig,
    TeamsNotificationConfigurationError,
    TeamsNotificationService,
)


def test_teams_notification_requires_an_https_webhook():
    service = TeamsNotificationService(TeamsNotificationConfig(webhook_url=""))

    with pytest.raises(TeamsNotificationConfigurationError, match="FS_TEAMS_WEBHOOK_URL"):
        service.prepare_service_sent_notification(service_number="2026_7001")


def test_teams_notification_prepares_the_service_number_message():
    service = TeamsNotificationService(
        TeamsNotificationConfig(webhook_url="https://example.test/teams-workflow")
    )

    prepared = service.prepare_service_sent_notification(service_number="2026_7001")

    body = prepared["payload"]["attachments"][0]["content"]["body"]
    assert prepared["service_number"] == "2026_7001"
    assert body[1]["text"] == "A folha nº 2026_7001 foi enviada ao cliente."


def test_teams_notification_posts_an_adaptive_card(monkeypatch):
    service = TeamsNotificationService(
        TeamsNotificationConfig(webhook_url="https://example.test/teams-workflow")
    )
    prepared = service.prepare_service_sent_notification(service_number="FS-123")
    captured = {}

    class FakeResponse:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def read():
            return b"accepted"

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr(teams_module.request, "urlopen", fake_urlopen)

    result = service.send_prepared(prepared, operation_id="teams:send:FS-123")

    assert result == {
        "accepted": True,
        "status_code": 202,
        "service_number": "FS-123",
    }
    assert captured["url"] == "https://example.test/teams-workflow"
    assert captured["timeout"] == 30
    assert captured["payload"]["type"] == "message"


def test_queue_notifies_teams_in_a_separate_job_after_mail(tmp_path):
    archive_dir = tmp_path / "Arquivadas" / "FS-TEAMS"
    archive_dir.mkdir(parents=True)
    archived_excel = archive_dir / "FS-TEAMS.xlsx"
    archived_excel.write_bytes(b"excel")
    events = []

    class FakeLocalPdf:
        @staticmethod
        def export_archive_pdf(path):
            events.append("pdf")
            pdf_path = Path(path).with_name("FS-TEAMS__folha_final.pdf")
            pdf_path.write_bytes(b"%PDF-1.7\nqueue")
            return pdf_path

    class FakeMail:
        @staticmethod
        def send_prepared(prepared, attachment_path, *, operation_id):
            events.append("mail")
            return {"accepted": True, "to": prepared["to"]}

    class FakeTeams:
        @staticmethod
        def send_prepared(prepared, *, operation_id):
            events.append("teams")
            return {
                "accepted": True,
                "service_number": prepared["service_number"],
            }

    queue = GraphSyncQueue(
        None,
        tmp_path / "mail-and-teams.sqlite3",
        mail_service=FakeMail(),
        local_pdf_service=FakeLocalPdf(),
        teams_notification_service=FakeTeams(),
    )
    parent = queue.enqueue(
        "archive_and_mail_local",
        {
            "archived_path": str(archived_excel),
            "mail": {"to": "cliente@example.com"},
            "teams": {
                "service_number": "FS-TEAMS",
                "payload": {"type": "message", "attachments": [{}]},
            },
        },
        job_id="send:FS-TEAMS:stable",
    )

    completed_parent = queue.wait(parent["id"], timeout=3)
    teams_job_id = completed_parent["result"]["teams_job"]["id"]
    completed_teams = queue.wait(teams_job_id, timeout=3)

    assert completed_parent["status"] == "complete"
    assert completed_parent["result"]["mail"]["accepted"] is True
    assert completed_teams["status"] == "complete"
    assert events == ["pdf", "mail", "teams"]