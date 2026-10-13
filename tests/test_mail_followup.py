"""M10 scopes supplement, and never replace, persisted job ownership."""
import re

import pytest

from src.services import admin_access
from tests.test_graph_job_visibility import service, login, make_job, OTHER_ID, ADMIN_ID, MARKER


def scope(client):
    response = client.get("/api/graph/monitor-context")
    assert response.status_code == 200
    assert "no-store" in response.headers["Cache-Control"]
    assert set(response.json) == {"success", "scope"}
    assert re.fullmatch(r"[a-f0-9]{64}", response.json["scope"])
    assert MARKER not in response.get_data(as_text=True)
    return response.json["scope"]


def test_context_is_stable_per_session_and_opaque(service):
    app, _ = service
    first, second = login(app), login(app)
    first_scope = scope(first)
    assert scope(first) == first_scope
    assert scope(second) != first_scope
    html = first.get("/").get_data(as_text=True)
    assert 'mailMonitorScope: "' + first_scope + '"' in html
    assert "js/mail-followup.js?v=" in html


def test_matching_scope_does_not_replace_ownership(service):
    app, queue = service
    identifier = make_job(queue, result={"mail": {"accepted": True}}, status="complete")
    owner, other = login(app), login(app, oid=OTHER_ID)
    good = owner.get("/api/graph/jobs/" + identifier, headers={"X-Mail-Monitor-Scope": scope(owner)})
    assert good.status_code == 200 and good.json["job"]["id"] == identifier
    assert MARKER not in good.get_data(as_text=True)
    denied = other.get("/api/graph/jobs/" + identifier, headers={"X-Mail-Monitor-Scope": scope(other)})
    assert denied.status_code == 403
    # Existing callers remain bound to their owner/admin authorization too.
    assert other.get("/api/graph/jobs/" + identifier).status_code == 403


@pytest.mark.parametrize("supplied", ["", "bad", "á", "0" * 64, "x" * 500])
def test_stale_or_invalid_scope_is_refused_before_queue_access(service, monkeypatch, supplied):
    app, queue = service
    client = login(app)
    scope(client)
    monkeypatch.setattr(queue, "status", lambda _id: pytest.fail("Queue accessed with invalid scope"))
    response = client.get("/api/graph/jobs/synthetic", headers={"X-Mail-Monitor-Scope": supplied})
    assert response.status_code == 403
    assert "sessão de acompanhamento mudou" in response.json["error"]
    assert "no-store" in response.headers["Cache-Control"]


def test_account_change_including_admin_invalidates_existing_monitor_before_queue(service, monkeypatch):
    app, queue = service
    client = login(app)
    previous = scope(client)
    with client.session_transaction() as session:
        session["microsoft_user"] = {**session["microsoft_user"], "id": ADMIN_ID,
                                     "email": admin_access.ADMIN_EMAIL}
    current = scope(client)
    assert previous != current
    monkeypatch.setattr(queue, "status", lambda _id: pytest.fail("Old account monitor reached queue"))
    response = client.get("/api/graph/jobs/synthetic", headers={"X-Mail-Monitor-Scope": previous})
    assert response.status_code == 403


def test_logout_and_new_login_of_same_person_uses_another_scope(service):
    app, _ = service
    client = login(app)
    previous = scope(client)
    with client.session_transaction() as session:
        user = dict(session["microsoft_user"])
    assert client.post("/logout").status_code == 302
    assert client.get("/api/graph/monitor-context").status_code == 401
    with client.session_transaction() as session:
        session["microsoft_user"] = user
    assert scope(client) != previous


def test_context_requires_auth_and_has_no_mutation_method(service):
    app, _ = service
    client = app.test_client()
    assert client.get("/api/graph/monitor-context").status_code == 401
    assert login(app).post("/api/graph/monitor-context").status_code == 405
