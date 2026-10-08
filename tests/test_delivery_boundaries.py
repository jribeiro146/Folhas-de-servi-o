"""No default external transport outside enabled production, even on direct calls."""
import json
import time

import pytest

import src.services.graph_mail_service as mail
import src.services.teams_notification_service as teams


class Response:
    status = 202

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b'{"access_token":"synthetic-token","expires_in":3600}'


def mail_service(**kwargs):
    return mail.GraphMailService(mail.GraphMailConfig("tenant", "client", "synthetic", "sender@example.invalid"),
                                 **kwargs)


def send_mail(service, tmp_path):
    path = tmp_path / "synthetic.pdf"
    path.write_bytes(b"%PDF-synthetic")
    return service.send_prepared({"to": "customer@example.invalid", "technician_email": "tech@example.invalid"},
                                 path, operation_id="synthetic")


@pytest.mark.parametrize("mode, enabled", [("test", False), ("test", True), ("development", True),
                                          ("production", False), ("invalid", True)])
@pytest.mark.parametrize("cached", [False, True])
def test_default_mail_transport_is_blocked_before_token_or_send(tmp_path, monkeypatch, mode, enabled, cached):
    monkeypatch.setenv("FS_ENVIRONMENT", mode)
    monkeypatch.setattr(mail, "MAIL_ENABLED", enabled, raising=False)
    calls = []
    monkeypatch.setattr(mail.request, "urlopen", lambda *args, **kwargs: calls.append(args) or Response())
    service = mail_service()
    if cached:
        service._access_token = "synthetic-cached"
        service._access_token_expires_at = time.time() + 60
    with pytest.raises(mail.GraphMailConfigurationError):
        send_mail(service, tmp_path)
    assert calls == []
    with pytest.raises(mail.GraphMailConfigurationError):
        service._request_bytes("https://example.invalid/direct", method="GET", authenticated=False)
    assert calls == []


@pytest.mark.parametrize("mode, enabled", [("test", False), ("test", True), ("development", True),
                                          ("production", False), ("invalid", True)])
def test_default_teams_transport_is_blocked_on_direct_send(monkeypatch, mode, enabled):
    monkeypatch.setenv("FS_ENVIRONMENT", mode)
    monkeypatch.setattr(teams, "TEAMS_NOTIFICATIONS_ENABLED", enabled, raising=False)
    calls = []
    monkeypatch.setattr(teams.request, "urlopen", lambda *args, **kwargs: calls.append(args) or Response())
    service = teams.TeamsNotificationService(teams.TeamsNotificationConfig("https://example.invalid/workflow"))
    prepared = service.prepare_service_sent_notification(service_number="synthetic")
    with pytest.raises(teams.TeamsNotificationConfigurationError):
        service.send_prepared(prepared, operation_id="synthetic")
    assert calls == []


def test_injected_mail_transport_exercises_token_and_real_envelope_in_test_mode(tmp_path):
    calls = []

    def transport(req, timeout):
        calls.append(req)
        return Response()
    result = send_mail(mail_service(transport=transport), tmp_path)
    assert result["accepted"] is True
    assert len(calls) == 2
    assert calls[0].full_url.endswith("/oauth2/v2.0/token")
    message = json.loads(calls[1].data)["message"]
    assert message["toRecipients"][0]["emailAddress"]["address"] == "customer@example.invalid"
    assert message["ccRecipients"][0]["emailAddress"]["address"] == "tech@example.invalid"


def test_injected_teams_transport_exercises_card_in_test_mode():
    calls = []

    def transport(req, timeout):
        calls.append(json.loads(req.data))
        return Response()
    service = teams.TeamsNotificationService(teams.TeamsNotificationConfig("https://example.invalid/workflow"),
                                            transport=transport)
    prepared = service.prepare_service_sent_notification(service_number="synthetic")
    assert service.send_prepared(prepared, operation_id="synthetic")["accepted"] is True
    assert calls == [prepared["payload"]]


def test_default_transport_allows_enabled_production_using_only_a_simulator(tmp_path, monkeypatch):
    monkeypatch.setenv("FS_ENVIRONMENT", "production")
    monkeypatch.setattr(mail, "MAIL_ENABLED", True, raising=False)
    monkeypatch.setattr(teams, "TEAMS_NOTIFICATIONS_ENABLED", True, raising=False)
    calls = []
    monkeypatch.setattr(mail.request, "urlopen", lambda req, **kwargs: calls.append(req.full_url) or Response())
    assert send_mail(mail_service(), tmp_path)["accepted"] is True
    service = teams.TeamsNotificationService(teams.TeamsNotificationConfig("https://example.invalid/workflow"))
    prepared = service.prepare_service_sent_notification(service_number="synthetic")
    assert service.send_prepared(prepared, operation_id="synthetic")["accepted"] is True
    assert len(calls) == 3
