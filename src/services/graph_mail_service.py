"""Envio de folhas de serviço por e-mail através do Microsoft Graph."""

from __future__ import annotations

import base64
import html
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, parse, request

from src.config import (
    GRAPH_CLIENT_ID,
    GRAPH_CLIENT_SECRET,
    GRAPH_TENANT_ID,
    MAIL_SENDER,
    MAIL_TEST_RECIPIENT,
)


GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
EMAIL_PATTERN = re.compile(r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$")
MAX_SIMPLE_ATTACHMENT_BYTES = 3 * 1024 * 1024


class GraphMailError(Exception):
    """Erro de validação ou comunicação no envio de e-mail."""

    retryable = False


class GraphMailTransientError(GraphMailError):
    """Falha temporária que pode ser repetida automaticamente de forma limitada."""

    retryable = True

    def __init__(self, message: str, *, retry_after_seconds: int | None = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class GraphMailConfigurationError(GraphMailError):
    """A configuração necessária para enviar e-mail está incompleta ou inválida."""


@dataclass(frozen=True)
class GraphMailConfig:
    tenant_id: str
    client_id: str
    client_secret: str
    sender: str
    test_recipient: str = ""

    @classmethod
    def from_env(cls) -> "GraphMailConfig":
        return cls(
            tenant_id=GRAPH_TENANT_ID,
            client_id=GRAPH_CLIENT_ID,
            client_secret=GRAPH_CLIENT_SECRET,
            sender=MAIL_SENDER,
            test_recipient=MAIL_TEST_RECIPIENT,
        )

    def missing_fields(self) -> list[str]:
        fields = {
            "GRAPH_TENANT_ID": self.tenant_id,
            "GRAPH_CLIENT_ID": self.client_id,
            "GRAPH_CLIENT_SECRET": self.client_secret,
            "FS_MAIL_SENDER": self.sender,
        }
        return [name for name, value in fields.items() if not value]


class GraphMailService:
    """Prepara e envia uma folha final em PDF usando ``/users/{sender}/sendMail``."""

    def __init__(self, config: GraphMailConfig | None = None):
        self.config = config or GraphMailConfig.from_env()
        self._access_token: str | None = None
        self._access_token_expires_at = 0.0

    def validate_config(self) -> None:
        missing = self.config.missing_fields()
        if missing:
            raise GraphMailConfigurationError(
                "Configuração de e-mail Microsoft Graph incompleta: " + ", ".join(missing)
            )
        try:
            self.validate_address(self.config.sender, label="remetente")
            if self.config.test_recipient:
                self.validate_address(self.config.test_recipient, label="destinatário de teste")
        except GraphMailError as exc:
            raise GraphMailConfigurationError(str(exc)) from exc

    def status(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "configured": not self.config.missing_fields(),
            "missing_fields": self.config.missing_fields(),
            "sender": self.config.sender or None,
            "test_mode": bool(self.config.test_recipient),
            "test_recipient": self.config.test_recipient or None,
        }

    def test_connection(self) -> dict[str, Any]:
        """Valida credenciais e ``Mail.Send`` sem criar nem enviar uma mensagem."""
        token = self._access_token_value()
        try:
            encoded_claims = token.split(".")[1]
            encoded_claims += "=" * (-len(encoded_claims) % 4)
            claims = json.loads(base64.urlsafe_b64decode(encoded_claims).decode("utf-8"))
        except (IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise GraphMailConfigurationError(
                "A Microsoft devolveu um token de acesso inválido para o serviço de e-mail."
            ) from exc

        roles = {str(role) for role in claims.get("roles") or []}
        if "Mail.Send" not in roles:
            raise GraphMailConfigurationError(
                "A aplicação Microsoft não possui a permissão de aplicação Mail.Send."
            )

        return {
            "authenticated": True,
            "mail_send_permission": True,
            "sender": self.config.sender,
            "application_id": str(claims.get("azp") or claims.get("appid") or ""),
            "tenant_id": str(claims.get("tid") or ""),
        }

    @staticmethod
    def validate_address(value: str, *, label: str = "e-mail") -> str:
        address = str(value or "").strip()
        if len(address) > 254 or not EMAIL_PATTERN.fullmatch(address):
            raise GraphMailError(f"O {label} não é válido.")
        return address

    def prepare_service_email(
        self,
        *,
        customer_email: str,
        technician_email: str,
        customer_name: str,
        service_number: str,
        document_language: str = "pt",
    ) -> dict[str, Any]:
        """Valida e fixa destinatários/conteúdo antes de a folha ser arquivada."""
        self.validate_config()
        customer_recipient = self.validate_address(customer_email, label="e-mail do cliente")
        recipient = (
            self.validate_address(self.config.test_recipient, label="destinatário de teste")
            if self.config.test_recipient
            else customer_recipient
        )
        technician = self.validate_address(technician_email, label="e-mail do técnico")

        cc_recipients: list[str] = []
        seen = {recipient.casefold()}
        for address in (technician,):
            key = address.casefold()
            if key not in seen:
                cc_recipients.append(address)
                seen.add(key)

        language = "en" if str(document_language).casefold() == "en" else "pt"
        safe_customer = html.escape(str(customer_name or "").strip() or "Cliente")
        safe_number = html.escape(str(service_number or "").strip() or "-")
        if language == "en":
            subject = f"Sensorpoint service sheet {service_number or ''}".strip()
            body_html = (
                f"<p>Dear {safe_customer},</p>"
                f"<p>Please find attached the service sheet for intervention "
                f"<strong>{safe_number}</strong>.</p>"
                "<p>Kind regards,<br>Sensorpoint</p>"
            )
        else:
            subject = f"Folha de serviço nº {service_number or ''} - Sensorpoint".strip()
            body_html = (
                "<p>Prezado(a) Senhor(a),</p>"
                f"<p>Em anexo, encaminhamos a folha de serviço relativa à intervenção "
                f"<strong>{safe_number}</strong>.</p>"
                "<p>Esta comunicação é enviada para um único endereço de e-mail. "
                "Caso considere necessário, agradecemos o seu reencaminhamento interno.</p>"
                "<p>Permanecemos à disposição para qualquer esclarecimento.</p>"
            )

        if self.config.test_recipient:
            subject = f"[TESTE] {subject}"

        return {
            "to": recipient,
            "cc": cc_recipients,
            "subject": subject,
            "body_html": body_html,
            "test_mode": bool(self.config.test_recipient),
            "technician_email": technician,
            "service_number": str(service_number or "").strip(),
        }

    def send_prepared(
        self,
        prepared: dict[str, Any],
        attachment_path: str | Path,
        *,
        operation_id: str,
    ) -> dict[str, Any]:
        """Envia uma mensagem já validada com o PDF final em anexo."""
        self.validate_config()
        prepared_recipient = self.validate_address(
            str(prepared.get("to") or ""),
            label="destinatário",
        )
        recipient = (
            self.validate_address(self.config.test_recipient, label="destinatário de teste")
            if self.config.test_recipient
            else prepared_recipient
        )
        # Nunca confiar na lista CC guardada por versões anteriores: ela podia
        # conter chefes de equipa. O único CC permitido é o técnico.
        technician_value = str(prepared.get("technician_email") or "").strip()
        if not technician_value:
            previous_cc = list(prepared.get("cc") or [])
            technician_value = str(previous_cc[0]) if previous_cc else ""
        technician = (
            self.validate_address(technician_value, label="e-mail do técnico")
            if technician_value
            else ""
        )
        cc_recipients: list[str] = []
        seen = {recipient.casefold()}
        if technician and technician.casefold() not in seen:
            cc_recipients.append(technician)
        subject = str(prepared.get("subject") or "Folha de serviço nº - Sensorpoint").strip()
        if self.config.test_recipient and not subject.startswith("[TESTE]"):
            subject = f"[TESTE] {subject}"
        body_html = str(prepared.get("body_html") or "").strip()

        pdf_path = Path(attachment_path)
        if not pdf_path.exists() or not pdf_path.is_file():
            raise GraphMailError(f"PDF da folha não encontrado: {pdf_path}")
        if pdf_path.suffix.casefold() != ".pdf":
            raise GraphMailError("O anexo da folha tem de ser exclusivamente um ficheiro PDF.")
        pdf_bytes = pdf_path.read_bytes()
        if not pdf_bytes.startswith(b"%PDF-"):
            raise GraphMailError("O anexo produzido não é um PDF válido.")
        if len(pdf_bytes) > MAX_SIMPLE_ATTACHMENT_BYTES:
            raise GraphMailError(
                "O PDF excede 3 MB e não pode ser enviado pelo modo de anexo simples do Graph."
            )

        correlation_id = re.sub(r"[^A-Za-z0-9._:-]+", "-", str(operation_id))[:200]
        payload = {
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": body_html},
                "toRecipients": [self._recipient(recipient)],
                "ccRecipients": [self._recipient(address) for address in cc_recipients],
                "internetMessageHeaders": [
                    {"name": "x-sensorpoint-operation-id", "value": correlation_id}
                ],
                "attachments": [
                    {
                        "@odata.type": "#microsoft.graph.fileAttachment",
                        "name": pdf_path.name,
                        "contentType": "application/pdf",
                        "contentBytes": base64.b64encode(pdf_bytes).decode("ascii"),
                    }
                ],
            },
            "saveToSentItems": True,
        }
        sender = parse.quote(self.config.sender, safe="@._-+")
        self._graph_bytes(
            "POST",
            f"/users/{sender}/sendMail",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            content_type="application/json",
        )
        return {
            "accepted": True,
            "sender": self.config.sender,
            "to": recipient,
            "cc": cc_recipients,
            "attachment": pdf_path.name,
        }

    @staticmethod
    def _recipient(address: str) -> dict[str, dict[str, str]]:
        return {"emailAddress": {"address": address}}

    def _access_token_value(self) -> str:
        self.validate_config()
        if self._access_token and time.time() < self._access_token_expires_at:
            return self._access_token

        token_url = TOKEN_URL_TEMPLATE.format(tenant_id=parse.quote(self.config.tenant_id))
        body = parse.urlencode(
            {
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "scope": GRAPH_SCOPE,
                "grant_type": "client_credentials",
            }
        ).encode("utf-8")
        response = self._request_json(
            token_url,
            method="POST",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            authenticated=False,
        )
        self._access_token = str(response["access_token"])
        self._access_token_expires_at = time.time() + int(response.get("expires_in") or 3600) - 120
        return self._access_token

    def _graph_bytes(
        self,
        method: str,
        path: str,
        *,
        data: bytes | None = None,
        content_type: str | None = None,
    ) -> bytes:
        headers = {"Accept": "application/json"}
        if content_type:
            headers["Content-Type"] = content_type
        return self._request_bytes(
            f"{GRAPH_ROOT}{path}",
            method=method,
            data=data,
            headers=headers,
        )

    def _request_json(
        self,
        url: str,
        *,
        method: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        authenticated: bool = True,
    ) -> dict[str, Any]:
        content = self._request_bytes(
            url,
            method=method,
            data=data,
            headers=headers,
            authenticated=authenticated,
        )
        return json.loads(content.decode("utf-8")) if content else {}

    def _request_bytes(
        self,
        url: str,
        *,
        method: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        authenticated: bool = True,
    ) -> bytes:
        request_headers = dict(headers or {})
        if authenticated:
            request_headers["Authorization"] = f"Bearer {self._access_token_value()}"
        req = request.Request(url, data=data, headers=request_headers, method=method)
        try:
            with request.urlopen(req, timeout=60) as response:
                return response.read()
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if not authenticated and exc.code in {400, 401, 403}:
                if "invalid_client" in detail or "AADSTS7000215" in detail:
                    raise GraphMailConfigurationError(
                        "A autenticação do serviço de e-mail falhou. Confirme que foi "
                        "configurado o Valor do client secret ativo, e não o ID do secret."
                    ) from exc
                raise GraphMailConfigurationError(
                    "A Microsoft rejeitou as credenciais configuradas para o serviço de e-mail."
                ) from exc
            if exc.code in {408, 425, 429} or 500 <= exc.code <= 599:
                retry_after = None
                try:
                    retry_after = max(int(exc.headers.get("Retry-After", "")), 0)
                except (AttributeError, TypeError, ValueError):
                    pass
                raise GraphMailTransientError(
                    f"O Microsoft Graph está temporariamente indisponível (HTTP {exc.code}).",
                    retry_after_seconds=retry_after,
                ) from exc
            raise GraphMailError(f"Microsoft Graph Mail HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise GraphMailTransientError(
                "O Microsoft Graph está temporariamente indisponível por falha de rede."
            ) from exc
