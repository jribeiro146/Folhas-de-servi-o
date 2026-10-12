"""Operational administrator, derived from the authenticated Microsoft account."""

from uuid import UUID

from src.config import ADMIN_OBJECT_ID, ADMIN_TENANT_ID

ADMIN_EMAIL = "jribeiro@sensorpoint.pt"


def _guid(value) -> str:
    if not isinstance(value, str):
        return ""
    try:
        normalized = str(UUID(value))
    except ValueError:
        return ""
    return normalized if normalized == value.lower() else ""


def admin_configuration_error() -> str | None:
    """Safe diagnosis: return a code, never the configured identity values."""
    if not ADMIN_TENANT_ID or not ADMIN_OBJECT_ID:
        return "admin_identity_not_configured"
    if not _guid(ADMIN_TENANT_ID) or not _guid(ADMIN_OBJECT_ID):
        return "admin_identity_config_invalid"
    return None


def account_role(email: str, *, tenant_id: str = "", object_id: str = "") -> str:
    # Recompute from the stable identity, including on old signed sessions.
    # An email/stored role alone must never restore elevated privileges.
    if (admin_configuration_error() is None and isinstance(email, str)
            and email.strip().casefold() == ADMIN_EMAIL
            and _guid(tenant_id) == _guid(ADMIN_TENANT_ID)
            and _guid(object_id) == _guid(ADMIN_OBJECT_ID)):
        return "special_admin"
    return "technician"


def can_administer(user) -> bool:
    return account_role(getattr(user, "email", ""), tenant_id=getattr(user, "tenant_id", ""),
                        object_id=getattr(user, "id", "")) == "special_admin"
