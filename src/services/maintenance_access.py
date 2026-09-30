"""Microsoft identity allowlist for SADI checklists."""

ALLOWED_EMAILS = frozenset({
    "acarvalho@sensorpoint.pt",
    "jribeiro@sensorpoint.pt",
})


def can_access_maintenance(user) -> bool:
    email = str(getattr(user, "email", "") or "").strip().casefold()
    return email in ALLOWED_EMAILS
