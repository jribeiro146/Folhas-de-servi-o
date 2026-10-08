"""Notificações de folhas enviadas através de um webhook do Teams Workflows."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib import error, parse, request

from src.config import TEAMS_WEBHOOK_URL, TEAMS_NOTIFICATIONS_ENABLED
from src.services.runtime_safety import require_live_delivery


class TeamsNotificationError(Exception):
    """Erro de configuração ou comunicação com o Teams."""


class TeamsNotificationConfigurationError(TeamsNotificationError):
    """O webhook necessário para notificar o Teams não está configurado."""


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

    def send_prepared(self, prepared: dict[str, Any], *, operation_id: str) -> dict[str, Any]:
        self.validate_config()
        transport = self._transport
        if transport is None:
            try:
                require_live_delivery(TEAMS_NOTIFICATIONS_ENABLED)
            except ValueError as exc:
                raise TeamsNotificationConfigurationError(str(exc)) from exc
            transport = request.urlopen
        payload = dict(prepared.get("payload") or {})
        if payload.get("type") != "message" or not payload.get("attachments"):
            raise TeamsNotificationError("A notificação Teams preparada não é válida.")

        correlation_id = re.sub(r"[^A-Za-z0-9._:-]+", "-", str(operation_id))[:200]
        req = request.Request(
            self.config.webhook_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "Sensorpoint-FolhasServico/1.0",
                "X-Sensorpoint-Operation-Id": correlation_id,
            },
            method="POST",
        )
        try:
            with transport(req, timeout=30) as response:
                response.read()
                status_code = int(response.status)
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise TeamsNotificationError(
                f"Teams Workflow HTTP {exc.code}: {detail}"
            ) from exc
        except error.URLError as exc:
            raise TeamsNotificationError(f"Teams Workflow indisponível: {exc}") from exc

        return {
            "accepted": 200 <= status_code < 300,
            "status_code": status_code,
            "service_number": str(prepared.get("service_number") or ""),
        }
