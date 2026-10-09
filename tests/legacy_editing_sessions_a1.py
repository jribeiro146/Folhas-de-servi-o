"""Exact method from a1cac3c0d3da8c7c712fce816cbc2b746e01ba21; synthetic fixture producer."""
import time
from typing import Any

class HistoricalEditingStateService:
    def _normalize_sessions(self, state: dict[str, Any], now: float | None = None) -> None:
        current_time = now or time.time()
        sessions = state.setdefault("sessions", {})
        legacy = state.get("lease")
        if isinstance(legacy, dict) and legacy.get("token"):
            token = str(legacy["token"])
            sessions.setdefault(token, dict(legacy))
            if not state.get("owner"):
                state["owner"] = {
                    "owner_id": legacy.get("owner_id"),
                    "owner_name": legacy.get("owner_name"),
                    "assigned_at": legacy.get("acquired_at") or current_time,
                }
        state["lease"] = None
        expired = [
            token
            for token, session in sessions.items()
            if float(session.get("expires_at") or 0) <= current_time
        ]
        for token in expired:
            sessions.pop(token, None)
