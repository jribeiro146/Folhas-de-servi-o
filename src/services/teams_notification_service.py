"""Notificações de folhas enviadas através de um webhook do Teams Workflows."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any
from urllib import error, parse, request

from src.config import TEAMS_WEBHOOK_URL, TEAMS_NOTIFICATIONS_ENABLED
from src.services.runtime_safety import require_live_delivery


class TeamsNotificationError(Exception):
    """Erro de configuração ou comunicação com o Teams."""

    retryable = False


class TeamsNotificationUnknownResultError(TeamsNotificationError):
    """O POST pode ter sido aceite; nunca repetir sem reconciliação."""


class TeamsNotificationThrottledError(TeamsNotificationError):
    retryable = True

    def __init__(self, delay: float):
        super().__init__("Teams Workflow recusou por limite de pedidos (HTTP 429).")
        self.retry_after_seconds = delay


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _retry_after(headers) -> float | None:
    value = str(headers.get("Retry-After", "")).strip() if headers is not None else ""
    try:
        if re.fullmatch(r"[0-9]{1,6}", value):
            delay = float(value)
        else:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                return None
            delay = max(0.0, parsed.timestamp() - time.time())
        # Longer/invalid delays require manual review, never an earlier retry.
        return delay if 0 <= delay <= 86400 else None
    except (ValueError, TypeError, OverflowError):
        return None


def _check_response(status: int, headers) -> None:
    if 200 <= status < 300:
        return
    if status == 429:
        delay = _retry_after(headers)
        if delay is not None:
            raise TeamsNotificationThrottledError(delay)
    if 400 <= status < 500 and status not in {408, 425}:
        raise TeamsNotificationError(f"Teams Workflow recusou o pedido (HTTP {status}).")
    raise TeamsNotificationUnknownResultError(
        f"Resultado Teams desconhecido (HTTP {status}); requer reconciliação."
    )

class TeamsNotificationConfigurationError(TeamsNotificationError):
    """O webhook necessário para notificar o Teams não está configurado."""

    retryable = False


@dataclass(frozen=True)
class TeamsNotificationConfig:
    webhook_url: str

    @classmethod
    def from_env(cls) -> "TeamsNotificationConfig":
        return cls(webhook_url=TEAMS_WEBHOOK_URL)


class TeamsNotificationService:
    """Publica um Adaptive Card num canal ou chat escolhido no Teams Workflow."""

    def __init__(self, config: TeamsNotificationConfig | None = None, *, transport=None):
        self.config = config or TeamsNotificationConfig.from_env()
        self._transport = transport

    def validate_config(self) -> None:
        value = self.config.webhook_url.strip()
        parsed = parse.urlparse(value)
        if not value:
            raise TeamsNotificationConfigurationError(
                "Notificações Teams ativas, mas FS_TEAMS_WEBHOOK_URL não está configurado."
            )
        if parsed.scheme.casefold() != "https" or not parsed.netloc:
            raise TeamsNotificationConfigurationError(
                "FS_TEAMS_WEBHOOK_URL tem de ser um URL HTTPS válido."
            )

    def status(self) -> dict[str, Any]:
        try:
            self.validate_config()
        except TeamsNotificationConfigurationError as exc:
            return {"enabled": True, "configured": False, "error": str(exc)}
        return {"enabled": True, "configured": True, "error": None}

    def prepare_service_sent_notification(self, *, service_number: str) -> dict[str, Any]:
        self.validate_config()
        number = str(service_number or "").strip() or "-"
        return {
            "service_number": number,
            "payload": {
                "type": "message",
                "attachments": [
                    {
                        "contentType": "application/vnd.microsoft.card.adaptive",
                        "contentUrl": None,
                        "content": {
                            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                            "type": "AdaptiveCard",
                            "version": "1.2",
                            "body": [
                                {
                                    "type": "TextBlock",
                                    "size": "Medium",
                                    "weight": "Bolder",
                                    "text": "Folha de serviço enviada",
                                },
                                {
                                    "type": "TextBlock",
                                    "wrap": True,
                                    "text": f"A folha nº {number} foi enviada ao cliente.",
                                },
                            ],
                        },
                    }
                ],
            },
        }

    def validate_prepared(self, prepared: dict[str, Any]) -> bytes:
        """Validate configuration and payload before recording a possible POST."""
        self.validate_config()
        if self._transport is None:
            try:
                require_live_delivery(TEAMS_NOTIFICATIONS_ENABLED)
            except ValueError as exc:
                raise TeamsNotificationConfigurationError(str(exc)) from exc
        payload = prepared.get("payload") if isinstance(prepared, dict) else None
        if (not isinstance(payload, dict) or payload.get("type") != "message"
                or not isinstance(payload.get("attachments"), list) or not payload["attachments"]
                or any(not isinstance(item, dict) for item in payload["attachments"])):
            raise TeamsNotificationError("A notificação Teams preparada não é válida.")
        try:
            return json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (ValueError, TypeError, UnicodeError):
            raise TeamsNotificationError("A notificação Teams preparada não é válida.") from None

    def send_prepared(self, prepared: dict[str, Any], *, operation_id: str) -> dict[str, Any]:
        encoded = self.validate_prepared(prepared)
        correlation_id = re.sub(r"[^A-Za-z0-9._:-]+", "-", str(operation_id))[:200]
        req = request.Request(
            self.config.webhook_url,
            data=encoded,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "Sensorpoint-FolhasServico/1.0",
                "X-Sensorpoint-Operation-Id": correlation_id,
            },
            method="POST",
        )
        transport = self._transport or request.build_opener(_NoRedirect()).open
        try:
            with transport(req, timeout=30) as response:
                status_code = int(response.status)
                _check_response(status_code, getattr(response, "headers", None))
        except error.HTTPError as exc:
            try:
                status_code = exc.code
                _check_response(exc.code, exc.headers)
            except TeamsNotificationError as classified:
                raise classified from None
            finally:
                exc.close()
        except TeamsNotificationError:
            raise
        except Exception:
            # Do not expose signed webhook URLs or provider response bodies.
            raise TeamsNotificationUnknownResultError(
                "Resultado Teams desconhecido após o pedido; requer reconciliação."
            ) from None

        return {
            "accepted": True,
            "status_code": status_code,
            "service_number": str(prepared.get("service_number") or ""),
        }
