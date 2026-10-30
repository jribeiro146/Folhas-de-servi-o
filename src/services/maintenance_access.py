"""Checklist access and the separate, existing cross-owner draft exception."""

FOREIGN_DRAFT_EDITORS = frozenset({
    "acarvalho@sensorpoint.pt",
    "jribeiro@sensorpoint.pt",
})


def can_access_maintenance(user) -> bool:
    """Accept an authenticated user resolved by the application's auth layer."""
    return user is not None


def can_edit_foreign_maintenance_draft(user) -> bool:
    """Opening checklists to everyone must not widen draft ownership rights."""
    email = str(getattr(user, "email", "") or "").strip().casefold()
    return email in FOREIGN_DRAFT_EDITORS
