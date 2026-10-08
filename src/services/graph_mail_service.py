"""Envio de folhas de serviço por e-mail através do Microsoft Graph."""

from __future__ import annotations

import base64
import html
import json
import re
import time
from http.client import HTTPException
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
    MAIL_ENABLED,
)
from src.services.runtime_safety import require_live_delivery


GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
EMAIL_PATTERN = re.compile(r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$")
MAX_SIMPLE_ATTACHMENT_BYTES = 3 * 1024 * 1024
# Conservative decimal limit: the provider documents 4 MB, without promising
# the larger binary interpretation or mailbox-specific acceptance.
MAX_SIMPLE_REQUEST_BYTES = 4_000_000


def _unique_json_members(pairs):
    result = {}
    seen = set()
    for key, value in pairs:
        normalized = key.casefold()
        if normalized in seen:
            raise ValueError("Ambiguous JSON member")
        seen.add(normalized)
        result[key] = value
    return result


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


class GraphMailUnknownResultError(GraphMailError):
    """The POST may have been accepted; repetition requires reconciliation."""


@dataclass(frozen=True)
class GraphMailDelivery:
    """Fixed message bytes and sending identity; contains no credentials."""

    payload: bytes
    sender: str
    tenant_id: str


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

    def __init__(self, config: GraphMailConfig | None = None, *, transport=None):
        self.config = config or GraphMailConfig.from_env()
        self._transport = transport
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

    def _validate_delivery_payload(self, data: bytes | None) -> None:
        """Validate the actual serialized request before credentials or delivery."""
        if not isinstance(data, bytes):
            raise GraphMailConfigurationError("A mensagem codificada de e-mail não é válida.")
        if len(data) > MAX_SIMPLE_REQUEST_BYTES:
            raise GraphMailError(
                "O e-mail excede 4 MB após codificação, incluindo corpo e anexos. "
                "Reduza o tamanho do conteúdo para usar o envio simples do Graph."
            )
        try:
            payload = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_json_members)
            if not isinstance(payload, dict) or not isinstance(payload.get("message"), dict):
                raise ValueError("Invalid message")
            if payload.keys() - {"message", "saveToSentItems"}:
                raise ValueError("Unknown payload member")
            message = payload["message"]
            if message.keys() - {"subject", "body", "toRecipients", "ccRecipients", "bccRecipients",
                                 "internetMessageHeaders", "attachments"}:
                raise ValueError("Unknown message member")
            if not message.get("toRecipients"):
                raise ValueError("Missing recipient")
            expected = (self.validate_address(self.config.test_recipient).casefold()
                        if self.config.test_recipient else None)
            for key in ("toRecipients", "ccRecipients", "bccRecipients"):
                recipients = message.get(key, [])
                if not isinstance(recipients, list):
                    raise ValueError("Invalid recipient list")
                for recipient in recipients:
                    if not isinstance(recipient, dict) or not isinstance(recipient.get("emailAddress"), dict):
                        raise ValueError("Invalid recipient")
                    value = recipient["emailAddress"].get("address")
                    if not isinstance(value, str):
                        raise ValueError("Invalid address")
                    address = self.validate_address(value)
                    if expected is not None and address.casefold() != expected:
                        raise ValueError("Recipient outside test mailbox")
        except (ValueError, TypeError, UnicodeError, RecursionError, GraphMailError) as exc:
            raise GraphMailConfigurationError(
                "Destinatários efetivos inválidos ou fora da caixa de teste autorizada."
            ) from exc

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
            if not self.config.test_recipient and key not in seen:
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
        """Compatibility entry point; prepare once, then send those exact bytes."""
        delivery = self.prepare_delivery(prepared, attachment_path, operation_id=operation_id)
        return self.send_delivery(delivery)

    def prepare_delivery(
        self,
        prepared: dict[str, Any],
        attachment_path: str | Path,
        *,
        operation_id: str,
    ) -> GraphMailDelivery:
        """Build and validate the final message without token or network I/O."""
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
        if not self.config.test_recipient and technician and technician.casefold() not in seen:
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
        try:
            with pdf_path.open("rb") as pdf_stream:
                pdf_bytes = pdf_stream.read(MAX_SIMPLE_ATTACHMENT_BYTES + 1)
        except OSError as exc:
            raise GraphMailError("Não foi possível ler o PDF para preparar o e-mail.") from exc
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
        payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        delivery = GraphMailDelivery(payload_bytes,
                                     self.validate_address(self.config.sender, label="remetente"),
                                     self.config.tenant_id.strip())
        self.validate_delivery(delivery)
        return delivery

    def validate_delivery(self, delivery: GraphMailDelivery) -> dict[str, Any]:
        """Recheck policy without changing bytes; return metadata, not acceptance."""
        self.validate_config()
        if self._transport is None:
            try:
                require_live_delivery(MAIL_ENABLED)
            except ValueError as exc:
                raise GraphMailConfigurationError(str(exc)) from exc
        if (not isinstance(delivery, GraphMailDelivery)
                or not isinstance(delivery.sender, str) or not isinstance(delivery.tenant_id, str)
                or delivery.sender.casefold() != self.validate_address(self.config.sender).casefold()
                or delivery.tenant_id.casefold() != self.config.tenant_id.strip().casefold()):
            raise GraphMailConfigurationError("A identidade de envio difere da mensagem preparada.")
        self._validate_delivery_payload(delivery.payload)
        try:
            payload = json.loads(delivery.payload.decode("utf-8"))
            message = payload["message"]
            if (payload.get("saveToSentItems") is not True
                    or len(message["toRecipients"]) != 1 or len(message.get("ccRecipients", [])) > 1
                    or "bccRecipients" in message
                    or not isinstance(message["subject"], str) or not message["subject"]
                    or not isinstance(message["body"], dict)
                    or message["body"].get("contentType") != "HTML"
                    or not isinstance(message["body"].get("content"), str)):
                raise ValueError("Invalid fixed service message")
            attachments = message["attachments"]
            if not isinstance(attachments, list) or len(attachments) != 1:
                raise ValueError("A service message requires one PDF")
            attachment = attachments[0]
            if (not isinstance(attachment, dict)
                    or attachment.get("@odata.type") != "#microsoft.graph.fileAttachment"
                    or attachment.get("contentType") != "application/pdf"
                    or not isinstance(attachment.get("name"), str)
                    or not attachment["name"].casefold().endswith(".pdf")
                    or not isinstance(attachment.get("contentBytes"), str)):
                raise ValueError("Invalid PDF attachment")
            pdf_bytes = base64.b64decode(attachment["contentBytes"], validate=True)
            if not pdf_bytes.startswith(b"%PDF-") or len(pdf_bytes) > MAX_SIMPLE_ATTACHMENT_BYTES:
                raise ValueError("Invalid PDF content")
            headers = message["internetMessageHeaders"]
            if (not isinstance(headers, list) or len(headers) != 1 or not isinstance(headers[0], dict)
                    or headers[0].get("name") != "x-sensorpoint-operation-id"
                    or not isinstance(headers[0].get("value"), str)
                    or not re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", headers[0]["value"])):
                raise ValueError("Invalid correlation header")
            return {
                "sender": delivery.sender,
                "to": message["toRecipients"][0]["emailAddress"]["address"],
                "cc": [item["emailAddress"]["address"] for item in message.get("ccRecipients", [])],
                "attachment": message["attachments"][0]["name"],
            }
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise GraphMailConfigurationError("A mensagem preparada está incompleta.") from exc

    def send_delivery(self, delivery: GraphMailDelivery) -> dict[str, Any]:
        """Send a fixed message; all metadata is checked before the POST."""
        metadata = self.validate_delivery(delivery)
        sender = parse.quote(delivery.sender, safe="@._-+")
        self._graph_bytes(
            "POST",
            f"/users/{sender}/sendMail",
            data=delivery.payload,
            content_type="application/json",
        )
        return {"accepted": True, **metadata}

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

    def _allowed_request_is_send(self, url: str, method: str, authenticated: bool) -> bool:
        """This service supports only its configured token and sendMail routes."""
        self.validate_config()
        token_url = TOKEN_URL_TEMPLATE.format(tenant_id=parse.quote(self.config.tenant_id))
        if method.upper() == "POST" and authenticated is False and url == token_url:
            return False
        target = parse.urlsplit(url)
        sender = self.validate_address(self.config.sender, label="remetente")
        expected = f"/v1.0/users/{sender}/sendMail".casefold()
        if (method.upper() == "POST" and authenticated is True
                and target.scheme == "https" and target.netloc.casefold() == "graph.microsoft.com"
                and not target.query and not target.fragment
                and parse.unquote(target.path).rstrip("/").casefold() == expected):
            return True
        raise GraphMailConfigurationError("Operação não permitida no transporte de e-mail configurado.")

    def _request_bytes(
        self,
        url: str,
        *,
        method: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        authenticated: bool = True,
    ) -> bytes:
        transport = self._transport
        if transport is None:
            try:
                require_live_delivery(MAIL_ENABLED)
            except ValueError as exc:
                raise GraphMailConfigurationError(str(exc)) from exc
            transport = request.urlopen
        is_send = self._allowed_request_is_send(url, method, authenticated)
        if is_send:
            self._validate_delivery_payload(data)
        request_headers = dict(headers or {})
        if authenticated:
            request_headers["Authorization"] = f"Bearer {self._access_token_value()}"
        req = request.Request(url, data=data, headers=request_headers, method=method)
        try:
            with transport(req, timeout=60) as response:
                return response.read()
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if is_send and (exc.code in {408, 425} or 500 <= exc.code <= 599):
                raise GraphMailUnknownResultError(
                    "Resultado do envio desconhecido. Confirme a aceitação antes de repetir."
                ) from exc
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
        except (error.URLError, OSError, HTTPException) as exc:
            if is_send:
                raise GraphMailUnknownResultError(
                    "Resultado do envio desconhecido após falha de comunicação. "
                    "Não repetir sem reconciliação."
                ) from exc
            raise GraphMailTransientError(
                "O Microsoft Graph está temporariamente indisponível por falha de rede."
            ) from exc
