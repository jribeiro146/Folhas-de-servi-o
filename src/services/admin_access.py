"""Operational administrator, derived from the authenticated Microsoft account."""

ADMIN_EMAIL = "jribeiro@sensorpoint.pt"


def account_role(email: str) -> str:
    return "special_admin" if str(email or "").strip().casefold() == ADMIN_EMAIL else "technician"


def can_administer(user) -> bool:
    return bool(getattr(user, "id", "")) and account_role(getattr(user, "email", "")) == "special_admin"
