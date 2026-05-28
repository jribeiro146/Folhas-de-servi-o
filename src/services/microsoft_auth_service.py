"""Microsoft Entra ID login helpers for company user authentication."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any
from urllib import error, parse, request

from src.config import (
    MICROSOFT_AUTH_ALLOWED_DOMAINS,
    MICROSOFT_AUTH_CLIENT_ID,
    MICROSOFT_AUTH_CLIENT_SECRET,
    MICROSOFT_AUTH_TENANT_ID,
)


AUTH_SCOPE = "openid profile email User.Read"
AUTHORIZE_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize"
TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
GRAPH_ME_URL = "https://graph.microsoft.com/v1.0/me?$select=id,displayName,mail,userPrincipalName"


class MicrosoftAuthError(Exception):
    """Raised when Microsoft login cannot be completed."""


@dataclass(frozen=True)
class MicrosoftAuthUser:
    id: str
    username: str
    initials: str
    display_name: str
    email: str
    role: str = "technician"


class MicrosoftAuthService:
    """OAuth/OIDC login flow for Microsoft Entra ID users."""

    def __init__(
        self,
        *,
        tenant_id: str = MICROSOFT_AUTH_TENANT_ID,
        client_id: str = MICROSOFT_AUTH_CLIENT_ID,
        client_secret: str = MICROSOFT_AUTH_CLIENT_SECRET,
        allowed_domains: list[str] | None = None,
    ):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.allowed_domains = allowed_domains or MICROSOFT_AUTH_ALLOWED_DOMAINS

    def validate_config(self) -> None:
        missing = []
        if not self.tenant_id:
            missing.append("MICROSOFT_AUTH_TENANT_ID")
        if not self.client_id:
            missing.append("MICROSOFT_AUTH_CLIENT_ID")
        if not self.client_secret:
            missing.append("MICROSOFT_AUTH_CLIENT_SECRET")
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
        claims = self._decode_unverified_jwt(str(token_payload.get("id_token") or ""))

        if claims.get("nonce") != nonce:
            raise MicrosoftAuthError("Resposta Microsoft inválida. Tente iniciar sessão novamente.")

        if str(claims.get("tid") or "").lower() != self.tenant_id.lower():
            raise MicrosoftAuthError("Esta conta Microsoft não pertence ao tenant configurado.")

        access_token = str(token_payload.get("access_token") or "")
        profile = self._load_profile(access_token) if access_token else {}

        email = self._first_value(
            profile.get("mail"),
            profile.get("userPrincipalName"),
            claims.get("email"),
            claims.get("preferred_username"),
            claims.get("upn"),
        )
        display_name = self._first_value(profile.get("displayName"), claims.get("name"), email)
        user_id = self._first_value(profile.get("id"), claims.get("oid"), email)

        if not email or not self._is_allowed_email(email):
            allowed = ", ".join(f"@{domain}" for domain in self.allowed_domains)
            raise MicrosoftAuthError(f"Acesso limitado a contas {allowed}.")

        return MicrosoftAuthUser(
            id=user_id,
            username=email,
            initials=self._initials_from_name(display_name or email),
            display_name=display_name or email,
            email=email,
        )

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
                role=str(payload.get("role") or "technician"),
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
            detail = exc.read().decode("utf-8", errors="replace")
            raise MicrosoftAuthError(f"Microsoft Login HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise MicrosoftAuthError(f"Microsoft Login indisponível: {exc}") from exc

        return json.loads(content.decode("utf-8")) if content else {}

    @staticmethod
    def _decode_unverified_jwt(token: str) -> dict[str, Any]:
        try:
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
            return json.loads(decoded.decode("utf-8"))
        except (IndexError, ValueError, json.JSONDecodeError) as exc:
            raise MicrosoftAuthError("Não foi possível validar a resposta Microsoft.") from exc

    def _is_allowed_email(self, email: str) -> bool:
        domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
        return domain in self.allowed_domains

    @staticmethod
    def _first_value(*values: object) -> str:
        for value in values:
            normalized = str(value or "").strip()
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
