"""Operational admin permissions and revocation over synthetic state only."""
import json
import time
from types import SimpleNamespace
import pytest
from src.services.admin_access import account_role, can_administer
from src.services.editing_state_service import (EditingStateService, EditorIdentity,
    EditingSessionRevokedError, RevisionConflictError, OperationInProgressError)
from src.services.file_mutex import file_mutex
from src.services.microsoft_auth_service import MicrosoftAuthService
import src.web.application as web

ADMIN = SimpleNamespace(id="admin-id", email="jribeiro@sensorpoint.pt", display_name="Administrador fictício")

def document(tmp_path):
    directory = tmp_path / "synthetic"
    directory.mkdir()
    file = directory / "synthetic.xlsx"
    file.write_bytes(b"synthetic")
    service = EditingStateService(tmp_path / "edits")
    identity = EditorIdentity("technician-id", "Técnico fictício")
    snapshot = service.acquire_lease(file, identity, "tab-1")
    return service, file, identity, snapshot

def version(service):
    return service.admin_documents()[0]["lock_version"]

def test_role_is_derived_from_exact_account_not_stored_role():
    auth = MicrosoftAuthService(tenant_id="synthetic", client_id="synthetic", client_secret="synthetic")
    base = {"id":"synthetic-id","username":"synthetic","display_name":"Synthetic"}
    for email, expected in [("jribeiro@sensorpoint.pt","special_admin"), (" JRIBEIRO@SENSORPOINT.PT ","special_admin"),
                            ("acarvalho@sensorpoint.pt","technician"), ("jribeiro@sensorpoint.pt.attacker.invalid","technician")]:
        user = auth.user_from_session({**base,"email":email,"role":"special_admin"})
        assert user.role == expected and can_administer(user) == (expected == "special_admin")
    assert not can_administer(None)

def test_release_revokes_old_tokens_and_reentry_but_preserves_autosave(tmp_path):
    service, file, editor, snapshot = document(tmp_path)
    doc, token = snapshot["document_id"], snapshot["lease"]["token"]
    service.save_autosave(document_id=doc, identity=editor, client_id="tab-1", lease_token=token,
                         base_revision=snapshot["revision"], document={"synthetic":"saved data"}, idempotency_key="first-autosave")
    before = service.snapshot_document(doc)
    service.admin_change_access(doc, ADMIN, "Recuperar reserva", version(service))
    after = service.snapshot_document(doc)
    assert after["owner"] is None and after["revision"] == before["revision"] + 1
    assert after["server_document"] == before["server_document"]
    with pytest.raises(EditingSessionRevokedError):
        service.renew_lease(doc, editor, "tab-1", token)
    with pytest.raises(EditingSessionRevokedError):
        service.acquire_lease(file, editor, "new-client-id")
    with pytest.raises(EditingSessionRevokedError):
        service.save_autosave(document_id=doc, identity=editor, client_id="tab-1", lease_token=token,
                              base_revision=after["revision"], document={"bad":"overwrite"}, idempotency_key="revoked-autosave")
    other = service.acquire_lease(file, EditorIdentity("other-id","Outro fictício"), "other-tab")
    assert other["lease"]["token"] != token
    assert service.repository.admin_history()[0]["action"] == "release_sheet"

def test_restore_allows_new_session_but_old_token_never_returns(tmp_path):
    service, file, editor, snapshot = document(tmp_path)
    doc = snapshot["document_id"]
    service.admin_change_access(doc, ADMIN, "Desbloquear", version(service))
    service.admin_change_access(doc, ADMIN, "Acesso reposto", version(service), restore_user=editor.id)
    new = service.acquire_lease(file, editor, "tab-1")
    assert new["lease"]["token"] != snapshot["lease"]["token"]


def test_admin_own_old_tab_is_also_revoked_without_losing_admin_capability(tmp_path):
    service, file, _, _ = document(tmp_path)
    with service._state_transaction(service.resolve_document_id(file)) as state:
        state["owner"] = None
        state["sessions"] = {}
    editor = EditorIdentity(ADMIN.id, "Administrador fictício")
    snapshot = service.acquire_lease(file, editor, "old-admin-tab")
    service.admin_change_access(snapshot["document_id"], ADMIN, "Terminar aba antiga", version(service))
    with pytest.raises(EditingSessionRevokedError):
        service.acquire_lease(file, editor, "old-admin-tab")
    assert can_administer(ADMIN)
    service.admin_change_access(snapshot["document_id"], ADMIN, "Repor o meu acesso", version(service), restore_user=ADMIN.id)
    assert service.acquire_lease(file, editor, "new-admin-tab")["lease"]["token"] != snapshot["lease"]["token"]

def test_stale_admin_list_cannot_kick_a_new_session(tmp_path):
    service, file, editor, snapshot = document(tmp_path)
    old = version(service)
    service.acquire_lease(file, editor, "second-tab")
    with pytest.raises(RevisionConflictError):
        service.admin_change_access(snapshot["document_id"], ADMIN, "Desbloquear", old)
    assert not service.repository.admin_history()
    assert service.snapshot_document(snapshot["document_id"])["owner"]["owner_id"] == editor.id

def test_active_write_and_pending_operation_prevent_release(tmp_path):
    import hashlib
    service, file, editor, snapshot = document(tmp_path)
    doc = snapshot["document_id"]
    lock = service.root / "operation-locks" / hashlib.sha256(doc.encode()).hexdigest()
    with file_mutex(lock):
        with pytest.raises(OperationInProgressError):
            service.admin_change_access(doc, ADMIN, "Desbloquear", version(service))
    service.claim_operation(document_id=doc, kind="send", idempotency_key="synthetic",
                            identity=editor, client_id="tab-1", lease_token=snapshot["lease"]["token"],
                            base_revision=snapshot["revision"])
    with pytest.raises(OperationInProgressError):
        service.admin_change_access(doc, ADMIN, "Desbloquear", version(service))
    assert not service.repository.admin_history()

def test_failed_audit_write_rolls_back_revocation(tmp_path, monkeypatch):
    service, _, _, snapshot = document(tmp_path)
    before = service.snapshot_document(snapshot["document_id"])
    monkeypatch.setattr(service.repository, "_insert_audit", lambda *a: (_ for _ in ()).throw(RuntimeError("synthetic failure")))
    with pytest.raises(RuntimeError):
        service.admin_change_access(snapshot["document_id"], ADMIN, "Desbloquear", version(service))
    assert service.snapshot_document(snapshot["document_id"]) == before

@pytest.fixture
def admin_app(tmp_path, monkeypatch):
    service, file, editor, snapshot = document(tmp_path)
    monkeypatch.setattr(web, "AUTH_ENABLED", True)
    monkeypatch.setattr(web, "ACTIVE_AUTH_PROVIDER", "microsoft")
    class Auth:
        def user_from_session(self, payload):
            return SimpleNamespace(**payload) if payload else None
    app = web.create_app(editing_state_service=service, microsoft_auth_service=Auth())
    return app, service, snapshot

def login(app, email, *, csrf=True):
    client = app.test_client()
    with client.session_transaction() as session:
        session["microsoft_user"] = {"id":"synthetic-"+email,"email":email,"display_name":"Synthetic","role":"special_admin"}
        if csrf:
            session["admin_csrf"] = "synthetic-csrf"
    return client

@pytest.mark.parametrize("email", ["other@sensorpoint.pt","acarvalho@sensorpoint.pt","jribeiro@sensorpoint.pt.attacker.invalid"])
def test_other_accounts_cannot_use_admin_even_with_forged_role(admin_app, email):
    app, _, snapshot = admin_app
    client = login(app, email)
    assert client.get("/api/admin/overview").status_code == 403
    assert client.post("/api/admin/documents/"+snapshot["document_id"]+"/release", json={},
                       headers={"X-Admin-CSRF":"synthetic-csrf"}).status_code == 403

def test_admin_page_exposes_csrf_only_after_authorization(admin_app):
    app, _, _ = admin_app
    admin = login(app, ADMIN.email, csrf=False)
    page = admin.get("/admin")
    assert page.status_code == 200 and page.headers["Cache-Control"] == "no-store"
    assert b'data-admin-csrf="' in page.data
    other = login(app, "other@sensorpoint.pt")
    assert other.get("/admin").status_code == 403

def test_unknown_document_cannot_create_state_or_audit(admin_app):
    app, service, _ = admin_app
    admin = login(app, ADMIN.email)
    before = service.repository.document_ids()
    response = admin.post("/api/admin/documents/unknown/release",
                          json={"reason":"Desbloquear","lock_version":"synthetic"},
                          headers={"X-Admin-CSRF":"synthetic-csrf"})
    assert response.status_code == 404
    assert service.repository.document_ids() == before and not service.repository.admin_history()

def test_non_admin_service_cannot_revoke_even_with_matching_version(tmp_path):
    service, _, _, snapshot = document(tmp_path)
    actor = SimpleNamespace(id="other", email="acarvalho@sensorpoint.pt", role="special_admin")
    with pytest.raises(PermissionError):
        service.admin_change_access(snapshot["document_id"], actor, "Desbloquear", version(service))
    assert not service.repository.admin_history()

@pytest.mark.parametrize("prefix", ["/api/admin/jobs/", "/api/graph/jobs/"])
@pytest.mark.parametrize("phase", ["sending_mail","mail_unknown",None,"sending_teams","teams_unknown","teams_throttled"])
def test_admin_cannot_retry_uncertain_mail(tmp_path, monkeypatch, phase, prefix):
    from src.services.graph_sync_queue import GraphSyncQueue
    monkeypatch.setattr(web,"AUTH_ENABLED",True)
    monkeypatch.setattr(web,"ACTIVE_AUTH_PROVIDER","microsoft")
    class Auth:
        def user_from_session(self,payload):
            return SimpleNamespace(**payload) if payload else None
    queue = GraphSyncQueue(None, tmp_path / "queue.sqlite3", auto_start=False)
    kind = "notify_teams" if phase in {"sending_teams", "teams_unknown", "teams_throttled"} else "archive_and_mail_local"
    job = queue.enqueue(kind, {"notification": {}} if kind == "notify_teams" else {"mail":{"to":"synthetic@example.invalid"}})
    result = {"phase":phase} if phase else {}
    if phase == "teams_throttled":
        result = {"phase": "teams_rejected", "http_status": 429, "retry_not_before": time.time() + 86400}
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET status='failed',result_json=? WHERE id=?", (json.dumps(result),job["id"]))
    service = EditingStateService(tmp_path / "edits")
    app = web.create_app(editing_state_service=service, graph_sync_queue=queue, microsoft_auth_service=Auth())
    admin = login(app,ADMIN.email)
    assert not admin.get("/api/admin/overview").get_json()["jobs"][0]["can_retry"]
    response = admin.post(prefix+job["id"]+"/retry", json={"reason":"Tentar recuperar"},
                          headers={"X-Admin-CSRF":"synthetic-csrf"})
    assert response.status_code == 409
    assert queue.status(job["id"])["attempts"] == 0
    assert not service.repository.admin_history()

@pytest.mark.parametrize("prefix", ["/api/admin/jobs/", "/api/graph/jobs/"])
def test_admin_retry_of_known_failure_is_audited_without_executing_worker(tmp_path, monkeypatch, prefix):
    from src.services.graph_sync_queue import GraphSyncQueue
    monkeypatch.setattr(web,"AUTH_ENABLED",True)
    monkeypatch.setattr(web,"ACTIVE_AUTH_PROVIDER","microsoft")
    class Auth:
        def user_from_session(self,payload):
            return SimpleNamespace(**payload) if payload else None
    queue = GraphSyncQueue(None, tmp_path / "queue.sqlite3", auto_start=False)
    job = queue.enqueue("remove_active", {"source_name":"synthetic.xlsx"})
    with queue._connect() as db:
        db.execute("UPDATE graph_sync_jobs SET status='failed',retryable=0 WHERE id=?", (job["id"],))
    service = EditingStateService(tmp_path / "edits")
    app = web.create_app(editing_state_service=service, graph_sync_queue=queue, microsoft_auth_service=Auth())
    admin = login(app,ADMIN.email)
    other = login(app, "other@sensorpoint.pt")
    assert other.post(prefix+job["id"]+"/retry", json={"reason":"Falha resolvida"},
                      headers={"X-Admin-CSRF":"synthetic-csrf"}).status_code == 403
    assert admin.post(prefix+job["id"]+"/retry", json={"reason":"Falha resolvida"}).status_code == 403
    assert admin.post(prefix+job["id"]+"/retry", json={},
                      headers={"X-Admin-CSRF":"synthetic-csrf"}).status_code == 409
    assert queue.status(job["id"])["status"] == "failed"
    assert not service.repository.admin_history()
    response = admin.post(prefix+job["id"]+"/retry", json={"reason":"Falha resolvida"},
                          headers={"X-Admin-CSRF":"synthetic-csrf"})
    assert response.status_code == 202
    assert queue.status(job["id"])["status"] == "pending"
    assert [entry["action"] for entry in service.repository.admin_history()] == ["retry_job_accepted","retry_job_requested"]

@pytest.mark.parametrize("headers", [{}, {"X-Admin-CSRF":"wrong"}, {"X-Admin-CSRF":"synthetic-csrf","Origin":"https://attacker.invalid"}])
def test_admin_mutations_reject_missing_token_and_cross_origin(admin_app, headers):
    app, service, snapshot = admin_app
    client = login(app, ADMIN.email)
    response = client.post("/api/admin/documents/"+snapshot["document_id"]+"/release",
                           json={"reason":"Desbloquear","lock_version":version(service)}, headers=headers)
    assert response.status_code == 403
    assert not service.repository.admin_history()

def test_admin_api_hides_tokens_and_document_content_and_performs_audited_release(admin_app):
    app, service, snapshot = admin_app
    client = login(app, ADMIN.email)
    response = client.get("/api/admin/overview")
    assert response.status_code == 200
    assert snapshot["lease"]["token"] not in response.get_data(as_text=True)
    data = response.get_json()["documents"][0]
    result = client.post("/api/admin/documents/"+snapshot["document_id"]+"/release",
                         json={"reason":"Desbloquear","lock_version":data["lock_version"]},
                         headers={"X-Admin-CSRF":"synthetic-csrf"})
    assert result.status_code == 200
    assert service.repository.admin_history()[0]["actor_email"] == ADMIN.email

def test_anonymous_mode_never_exposes_admin(tmp_path, monkeypatch):
    monkeypatch.setattr(web,"AUTH_ENABLED",False)
    monkeypatch.setattr(web,"ACTIVE_AUTH_PROVIDER","none")
    app = web.create_app(editing_state_service=EditingStateService(tmp_path / "edits"))
    client = login(app, ADMIN.email)
    assert client.get("/api/admin/overview").status_code == 403
