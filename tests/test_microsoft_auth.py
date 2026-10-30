"""OIDC consistency checks with invented claims and simulated TLS responses."""
import base64
import io
import json
import logging
from urllib.parse import parse_qs, urlsplit

import pytest

from src.services import microsoft_auth_service as auth_module
from src.services.microsoft_auth_service import CLAIM_CLOCK_SKEW_SECONDS, MicrosoftAuthError, MicrosoftAuthService

TENANT = "11111111-1111-4111-8111-111111111111"
CLIENT = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
OBJECT = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OTHER = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
NOW = 1000
NONCE = "synthetic-login-nonce"


def synthetic_jwt(claims):
    # Deliberately no valid signature: this tests claims consistency, not JWKS.
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"synthetic-header.{payload}.synthetic-signature"


@pytest.fixture
def identity(monkeypatch):
    monkeypatch.setattr(auth_module.time, "time", lambda: NOW)
    auth = MicrosoftAuthService(tenant_id=TENANT, client_id=CLIENT, client_secret="synthetic",
                                allowed_domains=["sensorpoint.pt"])
    claims = {"tid": TENANT, "oid": OBJECT, "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
              "aud": CLIENT, "nonce": NONCE, "exp": NOW + 60, "iat": NOW - 60, "nbf": NOW - 60,
              "email": "untrusted@sensorpoint.pt", "name": "Técnico fictício"}
    tokens = {"access_token": "synthetic-access", "id_token": synthetic_jwt(claims)}
    profile = {"id": OBJECT, "mail": "tecnico@sensorpoint.pt", "displayName": "Técnico fictício"}
    calls = []
    monkeypatch.setattr(auth, "_exchange_code", lambda **_kwargs: tokens)
    monkeypatch.setattr(auth, "_load_profile", lambda access: calls.append(access) or profile)
    return auth, claims, tokens, profile, calls


def authenticate(auth, *, nonce=NONCE):
    return auth.authenticate_code(code="synthetic-code", redirect_uri="https://app.example.invalid/callback",
                                  nonce=nonce)


def test_valid_login_uses_graph_identity_and_preserves_tenant_in_session(identity):
    auth, _, _, profile, calls = identity
    profile["id"] = OBJECT.upper()
    user = authenticate(auth)
    assert (user.id, user.tenant_id, user.email) == (OBJECT, TENANT, profile["mail"])
    assert calls == ["synthetic-access"]
    restored = auth.user_from_session(auth.user_to_session(user))
    assert restored == user


def test_domain_rejection_callback_explains_the_correct_account_without_exposing_it(identity, monkeypatch, caplog):
    import src.web.application as web
    from src.logging_config import JsonLogFormatter
    from urllib.parse import unquote
    auth, _, _, profile, _ = identity
    profile["mail"] = "private-person@example.invalid"
    monkeypatch.setattr(web, "ACTIVE_AUTH_PROVIDER", "microsoft")
    monkeypatch.setattr(web, "AUTH_ENABLED", True)
    client = web.create_app(microsoft_auth_service=auth).test_client()
    with client.session_transaction() as session:
        session.update(microsoft_auth_state="synthetic-state", microsoft_auth_nonce=NONCE)
    caplog.set_level(logging.ERROR)
    response = client.get("/auth/microsoft/callback?state=synthetic-state&code=synthetic-code")
    assert response.status_code == 302
    query = parse_qs(urlsplit(response.location).query)
    assert query["code"] == [auth_module.DOMAIN_NOT_ALLOWED_CODE]
    assert query["error"] == [auth_module.DOMAIN_NOT_ALLOWED_MESSAGE]
    assert query["reference"] == [response.headers["X-Request-ID"]]
    page = client.get(response.location).get_data(as_text=True)
    assert "conta de trabalho da Sensorpoint" in page and response.headers["X-Request-ID"] in page
    row = next(record for record in caplog.records if getattr(record, "event", None) == "auth_callback_failed")
    assert row.event_fields["cause"] == "account_domain_not_allowed"
    assert row.event_fields["auth_error_code"] == "account_domain_not_allowed"
    for exported in (unquote(response.location), page, JsonLogFormatter().format(row)):
        assert profile["mail"] not in exported and "synthetic-access" not in exported
    with client.session_transaction() as session:
        assert "microsoft_user" not in session


def test_missing_profile_email_remains_invalid_identity_without_domain_hint(identity):
    auth, _, _, profile, _ = identity
    profile.pop("mail")
    with pytest.raises(MicrosoftAuthError) as caught:
        authenticate(auth)
    assert caught.value.code != auth_module.DOMAIN_NOT_ALLOWED_CODE
    assert str(caught.value) == auth_module.INVALID_IDENTITY


@pytest.mark.parametrize("email,role", [("jribeiro@sensorpoint.pt", "special_admin"),
                                       ("acarvalho@sensorpoint.pt", "technician")])
def test_login_and_session_derive_role_from_configured_stable_identity(identity, monkeypatch, email, role):
    from src.services import admin_access
    auth, _, _, profile, _ = identity
    monkeypatch.setattr(admin_access, "ADMIN_TENANT_ID", TENANT)
    monkeypatch.setattr(admin_access, "ADMIN_OBJECT_ID", OBJECT)
    profile["mail"] = email
    user = authenticate(auth)
    assert user.role == role
    assert auth.user_from_session(auth.user_to_session(user)).role == role


@pytest.mark.parametrize("claim", ["iss", "aud", "exp", "nonce", "tid", "oid"])
def test_required_claim_missing_refuses_before_graph(identity, claim):
    auth, claims, tokens, _, calls = identity
    claims.pop(claim)
    tokens["id_token"] = synthetic_jwt(claims)
    with pytest.raises(MicrosoftAuthError):
        authenticate(auth)
    assert not calls


@pytest.mark.parametrize("claim,value", [
    ("iss", "https://attacker.example.invalid/v2.0"),
    ("iss", f"https://login.microsoftonline.com/{OTHER}/v2.0"),
    ("iss", f"https://sts.windows.net/{TENANT}/"),
    ("iss", [f"https://login.microsoftonline.com/{TENANT}/v2.0"]),
    ("aud", OTHER), ("aud", [CLIENT]), ("nonce", "other"), ("nonce", []),
    ("tid", OTHER), ("tid", "common"), ("tid", None), ("oid", "email@sensorpoint.pt"),
    ("oid", []), ("exp", NOW), ("exp", NOW - 1), ("exp", str(NOW + 60)),
    ("exp", True), ("exp", float("nan")), ("exp", float("inf")), ("exp", None),
    ("nbf", NOW + CLAIM_CLOCK_SKEW_SECONDS + 1), ("nbf", "0"), ("nbf", False),
    ("iat", NOW + CLAIM_CLOCK_SKEW_SECONDS + 1),
    ("iat", []), ("iat", float("nan")),
])
def test_invalid_claim_refuses_before_graph(identity, claim, value):
    auth, claims, tokens, _, calls = identity
    claims[claim] = value
    tokens["id_token"] = synthetic_jwt(claims)
    with pytest.raises(MicrosoftAuthError):
        authenticate(auth)
    assert not calls


@pytest.mark.parametrize("nonce", ["", None, []])
def test_expected_nonce_must_exist(identity, nonce):
    auth, claims, tokens, _, calls = identity
    claims["nonce"] = nonce
    tokens["id_token"] = synthetic_jwt(claims)
    with pytest.raises(MicrosoftAuthError):
        authenticate(auth, nonce=nonce)
    assert not calls


@pytest.mark.parametrize("offset", [2, CLAIM_CLOCK_SKEW_SECONDS])
def test_small_clock_offset_accepts_fresh_response(identity, offset):
    auth, claims, tokens, _, _ = identity
    claims.update(iat=NOW + offset, nbf=NOW + offset)
    tokens["id_token"] = synthetic_jwt(claims)
    assert authenticate(auth).id == OBJECT


def test_client_guid_casing_does_not_change_audience(identity):
    auth = identity[0]
    auth.client_id = CLIENT.upper()
    assert authenticate(auth).id == OBJECT


@pytest.mark.parametrize("token", [None, "", "a.b", "a.b.c.d", "a..c", "a.!!!.c", "a.é.c",
                                  "a._w.c", {}, 123, synthetic_jwt([]), synthetic_jwt(None)])
def test_malformed_id_token_fails_as_auth_error(identity, token):
    auth, _, tokens, _, calls = identity
    tokens["id_token"] = token
    with pytest.raises(MicrosoftAuthError):
        authenticate(auth)
    assert not calls


@pytest.mark.parametrize("token", [None, "", "   ", {}, 123])
def test_access_token_required_without_profile_fallback(identity, token):
    auth, _, tokens, _, calls = identity
    tokens["access_token"] = token
    with pytest.raises(MicrosoftAuthError):
        authenticate(auth)
    assert not calls


@pytest.mark.parametrize("profile", [None, [], {}, {"id": OTHER}, {"id": []},
                                    {"id": "email@sensorpoint.pt"}, {"mail": "tecnico@sensorpoint.pt"}])
def test_profile_missing_or_divergent_refuses(identity, monkeypatch, profile):
    auth = identity[0]
    monkeypatch.setattr(auth, "_load_profile", lambda _access: profile)
    with pytest.raises(MicrosoftAuthError):
        authenticate(auth)


def test_profile_failure_does_not_fall_back_to_claims(identity, monkeypatch):
    def fail(_access):
        raise MicrosoftAuthError("Synthetic Graph refusal")
    monkeypatch.setattr(identity[0], "_load_profile", fail)
    with pytest.raises(MicrosoftAuthError):
        authenticate(identity[0])


@pytest.mark.parametrize("mail,upn,accepted", [(None, "tecnico@sensorpoint.pt", True),
    (None, None, False), ({}, None, False), ("tecnico@other.example.invalid", None, False)])
def test_email_is_required_from_allowed_graph_profile(identity, mail, upn, accepted):
    auth, _, _, profile, _ = identity
    profile.update(mail=mail, userPrincipalName=upn)
    if accepted:
        assert authenticate(auth).email == upn
    else:
        with pytest.raises(MicrosoftAuthError):
            authenticate(auth)


@pytest.mark.parametrize("tokens", [None, [], "synthetic"])
def test_non_object_token_response_refused(identity, monkeypatch, tokens):
    auth = identity[0]
    monkeypatch.setattr(auth, "_exchange_code", lambda **_kwargs: tokens)
    with pytest.raises(MicrosoftAuthError):
        authenticate(auth)


@pytest.mark.parametrize("content", [b"[]", b"null", b"invalid-json", b"\xff"])
def test_malformed_http_json_is_an_auth_error(monkeypatch, content):
    monkeypatch.setattr(auth_module.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(content))
    with pytest.raises(MicrosoftAuthError):
        MicrosoftAuthService._request_json("https://example.invalid", method="GET")


@pytest.mark.parametrize("invalid", [False, True])
def test_callback_only_creates_session_after_complete_identity_validation(identity, monkeypatch, invalid):
    import src.web.application as web
    auth, claims, tokens, _, calls = identity
    if invalid:
        claims["aud"] = OTHER
        tokens["id_token"] = synthetic_jwt(claims)
    monkeypatch.setattr(web, "ACTIVE_AUTH_PROVIDER", "microsoft")
    monkeypatch.setattr(web, "AUTH_ENABLED", True)
    app = web.create_app(microsoft_auth_service=auth)
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(microsoft_auth_state="synthetic-state", microsoft_auth_nonce=NONCE,
                       microsoft_auth_next="/", preserved="synthetic")
    response = client.get("/auth/microsoft/callback?state=synthetic-state&code=synthetic-code")
    assert response.status_code == 302
    with client.session_transaction() as session:
        assert "microsoft_auth_nonce" not in session
        if invalid:
            assert "microsoft_user" not in session
            assert session["preserved"] == "synthetic"
            assert not calls
            assert urlsplit(response.location).path == "/login"
        else:
            assert session["microsoft_user"]["id"] == OBJECT
            assert session["microsoft_user"]["tenant_id"] == TENANT
            assert "preserved" not in session
            assert response.location == "/"


def test_authorization_request_keeps_state_nonce_and_v2_scope(identity):
    auth = identity[0]
    url = auth.build_authorization_url(redirect_uri="https://app.example.invalid/callback",
                                       state="synthetic-state", nonce=NONCE)
    parsed = urlsplit(url)
    assert parsed.path == f"/{TENANT}/oauth2/v2.0/authorize"
    query = parse_qs(parsed.query)
    assert query["nonce"] == [NONCE] and query["state"] == ["synthetic-state"]
    assert query["response_type"] == ["code"]
    assert query["scope"] == ["openid profile email User.Read"]


def test_callback_records_only_safe_time_failure_code(identity, monkeypatch, caplog):
    import src.web.application as web
    from src.logging_config import JsonLogFormatter
    auth, claims, tokens, _, calls = identity
    claims["nbf"] = NOW + CLAIM_CLOCK_SKEW_SECONDS + 1
    tokens["id_token"] = synthetic_jwt(claims)
    monkeypatch.setattr(web, "ACTIVE_AUTH_PROVIDER", "microsoft")
    monkeypatch.setattr(web, "AUTH_ENABLED", True)
    client = web.create_app(microsoft_auth_service=auth).test_client()
    with client.session_transaction() as session:
        session.update(microsoft_auth_state="synthetic-state", microsoft_auth_nonce=NONCE)
    caplog.set_level(logging.ERROR)
    response = client.get("/auth/microsoft/callback?state=synthetic-state&code=synthetic-code")
    assert response.status_code == 302 and not calls
    fields = [r.event_fields for r in caplog.records if getattr(r, "event", None) == "auth_callback_failed"]
    assert fields == [{"error_type": "MicrosoftAuthError", "auth_error_code": "identity_claim_time_invalid",
                       "cause": "identity_invalid"}]
    output = "\n".join(JsonLogFormatter().format(record) for record in caplog.records)
    for marker in (NONCE, tokens["id_token"], tokens["access_token"], OBJECT, TENANT, "untrusted@sensorpoint.pt"):
        assert marker not in output
    with client.session_transaction() as session:
        assert "microsoft_user" not in session
