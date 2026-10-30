"""Microsoft Entra ID login helpers for company user authentication."""

from __future__ import annotations

import base64
import binascii
import json
import math
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID
from src.services.admin_access import account_role
from src.services.diagnostic_views import known_provider_cause
from urllib import error, parse, request

from src.config import (
    GRAPH_CLIENT_ID,
    GRAPH_CLIENT_SECRET,
    GRAPH_TENANT_ID,
    MICROSOFT_AUTH_ALLOWED_DOMAINS,
)


AUTH_SCOPE = "openid profile email User.Read"
AUTHORIZE_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize"
TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
GRAPH_ME_URL = "https://graph.microsoft.com/v1.0/me?$select=id,displayName,mail,userPrincipalName"
INVALID_IDENTITY = "Resposta Microsoft inválida. Tente iniciar sessão novamente."
DOMAIN_NOT_ALLOWED_CODE = "account_domain_not_allowed"
DOMAIN_NOT_ALLOWED_MESSAGE = (
    "A conta escolhida não pertence a um domínio permitido. "
    "Inicie sessão com a sua conta de trabalho da Sensorpoint."
)
CLAIM_CLOCK_SKEW_SECONDS = 60


class MicrosoftAuthError(Exception):
    """Raised when Microsoft login cannot be completed."""

    def __init__(self, message: str, *, code: str = "microsoft_auth_failed", diagnostic_cause=None):
        super().__init__(message)
        self.code = code
        self.diagnostic_cause = diagnostic_cause


@dataclass(frozen=True)
class MicrosoftAuthUser:
    id: str
    username: str
    initials: str
    display_name: str
    email: str
    role: str = "technician"
    tenant_id: str = ""


class MicrosoftAuthService:
    """OAuth/OIDC login flow for Microsoft Entra ID users."""

    def __init__(
        self,
        *,
        tenant_id: str = GRAPH_TENANT_ID,
        client_id: str = GRAPH_CLIENT_ID,
        client_secret: str = GRAPH_CLIENT_SECRET,
        allowed_domains: list[str] | None = None,
    ):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.allowed_domains = allowed_domains or MICROSOFT_AUTH_ALLOWED_DOMAINS

    def validate_config(self) -> None:
        missing = []
        if not self.tenant_id:
            missing.append("GRAPH_TENANT_ID")
        if not self.client_id:
            missing.append("GRAPH_CLIENT_ID")
        if not self.client_secret:
            missing.append("GRAPH_CLIENT_SECRET")
        if missing:
            raise MicrosoftAuthError("Configuração Microsoft Login incompleta: " + ", ".join(missing))

    def build_authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        nonce: str,
    ) -> str:
        self.validate_config()
        query = parse.urlencode(
            {
                "client_id": self.client_id,
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "response_mode": "query",
                "scope": AUTH_SCOPE,
                "state": state,
                "nonce": nonce,
                "prompt": "select_account",
            }
        )
        return f"{AUTHORIZE_URL_TEMPLATE.format(tenant_id=parse.quote(self.tenant_id))}?{query}"

    def authenticate_code(self, *, code: str, redirect_uri: str, nonce: str) -> MicrosoftAuthUser:
        self.validate_config()
        token_payload = self._exchange_code(code=code, redirect_uri=redirect_uri)
        if not isinstance(token_payload, dict):
            raise MicrosoftAuthError(INVALID_IDENTITY)
        claims = self._decode_unverified_jwt(token_payload.get("id_token"))
        tenant_id, object_id = self._validate_identity_claims(claims, nonce=nonce)
        access_token = token_payload.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            raise MicrosoftAuthError(INVALID_IDENTITY)
        profile = self._load_profile(access_token)
        # /me is required proof from Graph using the delegated token. Never use
        # a claim/email as a fallback for a missing or different Graph identity.
        if not isinstance(profile, dict) or self._identity_uuid(profile.get("id")) != object_id:
            raise MicrosoftAuthError(INVALID_IDENTITY)

        email = self._first_value(
            profile.get("mail"),
            profile.get("userPrincipalName"),
        )
        display_name = self._first_value(profile.get("displayName"), claims.get("name"), email)

        if not email:
            raise MicrosoftAuthError(INVALID_IDENTITY)
        if not self._is_allowed_email(email):
            # This fixed, actionable message never includes the chosen account
            # or dynamic provider/configuration text. Authorization is unchanged.
            raise MicrosoftAuthError(DOMAIN_NOT_ALLOWED_MESSAGE, code=DOMAIN_NOT_ALLOWED_CODE)

        return MicrosoftAuthUser(
            id=object_id,
            username=email,
            initials=self._initials_from_name(display_name or email),
            display_name=display_name or email,
            email=email,
            role=account_role(email, tenant_id=tenant_id, object_id=object_id),
            tenant_id=tenant_id,
        )

    def _validate_identity_claims(self, claims: dict[str, Any], *, nonce: str) -> tuple[str, str]:
        """Check consistency of the v2 token obtained directly over TLS.

        This does not verify its signature. Do not reuse this helper to accept
        externally supplied JWTs or bearer authentication on application routes.
        """
        tenant_id = self._identity_uuid(claims.get("tid"))
        if tenant_id != self._identity_uuid(self.tenant_id):
            raise MicrosoftAuthError(INVALID_IDENTITY)
        if (claims.get("iss") != f"https://login.microsoftonline.com/{tenant_id}/v2.0"
                or self._identity_uuid(claims.get("aud")) != self._identity_uuid(self.client_id)
                or not isinstance(nonce, str) or not nonce
                or claims.get("nonce") != nonce):
            raise MicrosoftAuthError(INVALID_IDENTITY)
        now = time.time()
        # No expiry grace: an expired response must initiate a fresh login.
        # Optional nbf/iat tolerate a small clock offset to avoid rejecting a
        # fresh Microsoft response when the local clock is slightly behind.
        for name in ("exp", "nbf", "iat"):
            if name != "exp" and name not in claims:
                continue
            value = claims.get(name)
            if (type(value) not in (int, float) or (isinstance(value, float) and not math.isfinite(value))
                    or (name == "exp" and value <= now)
                    or (name != "exp" and value > now + CLAIM_CLOCK_SKEW_SECONDS)):
                raise MicrosoftAuthError(INVALID_IDENTITY, code="identity_claim_time_invalid")
        return tenant_id, self._identity_uuid(claims.get("oid"))

    @staticmethod
    def _identity_uuid(value: Any) -> str:
        if not isinstance(value, str):
            raise MicrosoftAuthError(INVALID_IDENTITY)
        try:
            normalized = str(UUID(value))
        except ValueError as exc:
            raise MicrosoftAuthError(INVALID_IDENTITY) from exc
        if normalized != value.lower():
            raise MicrosoftAuthError(INVALID_IDENTITY)
        return normalized

    def user_from_session(self, payload: dict[str, Any] | None) -> MicrosoftAuthUser | None:
        if not payload:
            return None
        try:
            return MicrosoftAuthUser(
                id=str(payload["id"]),
                username=str(payload["username"]),
                initials=str(payload.get("initials") or ""),
                display_name=str(payload["display_name"]),
                email=str(payload["email"]),
                role=account_role(payload["email"], tenant_id=payload.get("tenant_id", ""), object_id=payload["id"]),
                tenant_id=str(payload.get("tenant_id") or ""),
            )
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def user_to_session(user: MicrosoftAuthUser) -> dict[str, str]:
        return {
            "id": user.id,
            "username": user.username,
            "initials": user.initials,
            "display_name": user.display_name,
            "email": user.email,
            "role": user.role,
            "tenant_id": user.tenant_id,
        }

    def _exchange_code(self, *, code: str, redirect_uri: str) -> dict[str, Any]:
        body = parse.urlencode(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "scope": AUTH_SCOPE,
            }
        ).encode("utf-8")
        return self._request_json(
            TOKEN_URL_TEMPLATE.format(tenant_id=parse.quote(self.tenant_id)),
            method="POST",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    def _load_profile(self, access_token: str) -> dict[str, Any]:
        return self._request_json(
            GRAPH_ME_URL,
            method="GET",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )

    @staticmethod
    def _request_json(
        url: str,
        *,
        method: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        req = request.Request(url, data=data, headers=headers or {}, method=method)
        try:
            with request.urlopen(req, timeout=60) as response:
                content = response.read()
        except error.HTTPError as exc:
            # Retain only an enumerated hint; never retain the provider body.
            try:
                cause = known_provider_cause(exc.read(65536).decode("utf-8", errors="replace"))
            except (OSError, ValueError):
                cause = None
            raise MicrosoftAuthError("O fornecedor Microsoft não concluiu o pedido de login.",
                                     code="microsoft_http_failed", diagnostic_cause=cause) from exc
        except error.URLError as exc:
            raise MicrosoftAuthError("Microsoft Login temporariamente indisponível.",
                                     code="microsoft_unavailable") from exc

        try:
            payload = json.loads(content.decode("utf-8")) if content else {}
        except (ValueError, UnicodeError) as exc:
            raise MicrosoftAuthError(INVALID_IDENTITY) from exc
        if not isinstance(payload, dict):
            raise MicrosoftAuthError(INVALID_IDENTITY)
        return payload

    @staticmethod
    def _decode_unverified_jwt(token: Any) -> dict[str, Any]:
        try:
            if not isinstance(token, str):
                raise ValueError()
            parts = token.split(".")
            if len(parts) != 3 or not all(parts):
                raise ValueError()
            payload = parts[1]
            payload += "=" * (-len(payload) % 4)
            decoded = base64.b64decode(payload.encode("ascii"), altchars=b"-_", validate=True)
            claims = json.loads(decoded.decode("utf-8"))
            if not isinstance(claims, dict):
                raise ValueError()
            return claims
        except (ValueError, UnicodeError, binascii.Error) as exc:
            raise MicrosoftAuthError(INVALID_IDENTITY) from exc

    def _is_allowed_email(self, email: str) -> bool:
        domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
        return domain in self.allowed_domains

    @staticmethod
    def _first_value(*values: object) -> str:
        for value in values:
            normalized = value.strip() if isinstance(value, str) else ""
            if normalized:
                return normalized
        return ""

    @staticmethod
    def _initials_from_name(value: str) -> str:
        pieces = [piece for piece in value.replace("@", " ").replace(".", " ").split() if piece]
        if not pieces:
            return ""
        if len(pieces) == 1:
            return pieces[0][:2].upper()
        return f"{pieces[0][0]}{pieces[-1][0]}".upper()
