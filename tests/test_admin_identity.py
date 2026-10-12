"""Stable administrator identity, with invented GUIDs and no operational configuration."""
import json
import logging
from types import SimpleNamespace

import pytest

from src.services import admin_access
from src.services.admin_access import account_role, admin_configuration_error, can_administer
from src.services.microsoft_auth_service import MicrosoftAuthService
from src.services.maintenance_access import can_access_maintenance

TENANT = "11111111-1111-4111-8111-111111111111"
OBJECT = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OTHER = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


@pytest.fixture
def configured_admin(monkeypatch):
    monkeypatch.setattr(admin_access, "ADMIN_TENANT_ID", TENANT)
    monkeypatch.setattr(admin_access, "ADMIN_OBJECT_ID", OBJECT)
    return SimpleNamespace(id=OBJECT, tenant_id=TENANT, email=admin_access.ADMIN_EMAIL,
                           display_name="Administrador fictício", username="synthetic", initials="AF")


def test_exact_identity_ignores_stored_role_and_accepts_guid_casing(configured_admin, monkeypatch):
    user = configured_admin
    user.role = "technician"
    assert can_administer(user)
    user.id = OBJECT.upper()
    monkeypatch.setattr(admin_access, "ADMIN_OBJECT_ID", OBJECT.upper())
    assert can_administer(user)
    assert account_role(user.email) == "technician"  # Email alone has no privilege.
    assert not can_administer(None)


@pytest.mark.parametrize("field,value", [
    ("id", OTHER), ("id", ""), ("id", None), ("id", []),
    ("tenant_id", OTHER), ("tenant_id", ""), ("tenant_id", None), ("tenant_id", []),
    ("email", "acarvalho@sensorpoint.pt"), ("email", "tecnico@sensorpoint.pt"),
    ("email", "jribeiro@sensorpoint.pt.attacker.invalid"), ("email", []),
])
def test_identity_mismatch_cannot_administer_even_with_forged_role(configured_admin, field, value):
    user = configured_admin
    user.role = "special_admin"
    setattr(user, field, value)
    assert not can_administer(user)


@pytest.mark.parametrize("tenant,object_id,code", [
    ("", OBJECT, "admin_identity_not_configured"),
    (TENANT, "", "admin_identity_not_configured"),
    ("", "", "admin_identity_not_configured"),
    ("tenant.invalid", OBJECT, "admin_identity_config_invalid"),
    (TENANT, "object.invalid", "admin_identity_config_invalid"),
])
def test_missing_or_invalid_configuration_refuses_privilege(configured_admin, monkeypatch, tenant, object_id, code):
    monkeypatch.setattr(admin_access, "ADMIN_TENANT_ID", tenant)
    monkeypatch.setattr(admin_access, "ADMIN_OBJECT_ID", object_id)
    assert admin_configuration_error() == code
    assert not can_administer(configured_admin)


@pytest.mark.parametrize("email", ["jribeiro@sensorpoint.pt", "acarvalho@sensorpoint.pt"])
def test_sadi_personal_access_survives_missing_admin_configuration(configured_admin, monkeypatch, email):
    configured_admin.email = email
    monkeypatch.setattr(admin_access, "ADMIN_OBJECT_ID", "")
    assert can_access_maintenance(configured_admin)
    assert not can_administer(configured_admin)
    configured_admin.email = "tecnico@sensorpoint.pt"
    assert not can_access_maintenance(configured_admin)


@pytest.mark.parametrize("missing", [None, "tenant_id", "id"])
def test_session_role_recomputed_and_legacy_incomplete_session_not_elevated(configured_admin, missing):
    auth = MicrosoftAuthService(tenant_id=TENANT, client_id=OTHER, client_secret="synthetic")
    payload = vars(configured_admin).copy()
    payload["role"] = "special_admin"
    if missing:
        payload.pop(missing)
    user = auth.user_from_session(payload)
    if missing == "id":
        assert user is None
    else:
        assert user.role == ("special_admin" if not missing else "technician")
        assert can_administer(user) is (missing is None)
        assert can_access_maintenance(user)


def test_old_email_only_session_does_not_auto_enroll(configured_admin, monkeypatch):
    auth = MicrosoftAuthService(tenant_id=TENANT, client_id=OTHER, client_secret="synthetic")
    monkeypatch.setattr(admin_access, "ADMIN_TENANT_ID", "")
    monkeypatch.setattr(admin_access, "ADMIN_OBJECT_ID", "")
    for _ in range(2):
        user = auth.user_from_session({**vars(configured_admin), "role": "special_admin"})
        assert user.role == "technician"
    assert not admin_access.ADMIN_TENANT_ID and not admin_access.ADMIN_OBJECT_ID


@pytest.mark.parametrize("change", [None, "id", "tenant_id", "legacy", "missing_config"])
def test_real_session_parser_and_admin_routes_require_stable_identity(configured_admin, monkeypatch, change):
    import src.web.application as web
    monkeypatch.setattr(web, "ACTIVE_AUTH_PROVIDER", "microsoft")
    monkeypatch.setattr(web, "AUTH_ENABLED", True)
    if change == "missing_config":
        monkeypatch.setattr(admin_access, "ADMIN_OBJECT_ID", "")
    auth = MicrosoftAuthService(tenant_id=TENANT, client_id=OTHER, client_secret="synthetic")
    app = web.create_app(microsoft_auth_service=auth)
    client = app.test_client()
    payload = {**vars(configured_admin), "role": "special_admin"}
    if change in ("id", "tenant_id"):
        payload[change] = OTHER
    elif change == "legacy":
        payload.pop("tenant_id")
    with client.session_transaction() as session:
        session["microsoft_user"] = payload
        session["admin_csrf"] = "synthetic-csrf"
    response = client.get("/api/admin/overview")
    assert response.status_code == (200 if change is None else 403)
    if change:
        assert response.get_json()["error_code"] == (
            "admin_identity_not_configured" if change == "missing_config" else "admin_access_denied")
        result = client.post("/api/admin/documents/synthetic/release", json={"reason": "Synthetic"},
                             headers={"X-Admin-CSRF": "synthetic-csrf"})
        assert result.status_code == 403


def test_missing_configuration_diagnostic_contains_no_identity(configured_admin, monkeypatch, caplog):
    import src.web.application as web
    from src.logging_config import JsonLogFormatter
    monkeypatch.setattr(admin_access, "ADMIN_OBJECT_ID", "")
    caplog.set_level(logging.WARNING)
    app = web.create_app()
    fields = [r.event_fields for r in caplog.records if getattr(r, "event", None) == "admin_identity_configuration"]
    assert fields == [{"error_code": "admin_identity_not_configured"}]
    response = app.test_client().get("/api/admin/overview")
    assert response.status_code == 403
    text = "\n".join(JsonLogFormatter().format(record) for record in caplog.records)
    for marker in (TENANT, OBJECT, configured_admin.email):
        assert marker not in text
        assert marker not in json.dumps(response.get_json())
