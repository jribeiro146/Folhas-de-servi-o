"""Synthetic aggregate shapes from versioned source, never operational records.

v1: _new_state / acquire_lease in f409af0b0ea77b11374954325e02779529aaa1e8.
v2: _new_state in a1cac3c0d3da8c7c712fce816cbc2b746e01ba21.
Values below are fictitious; names/fields follow those source functions.
"""


def version_one(document_id):
    return {"version": 1, "document_id": document_id, "revision": 1,
            "etag": "", "fingerprint": "", "aliases": [], "lease": None,
            "autosave": None, "operations": {}}


def version_two(document_id):
    return {**version_one(document_id), "version": 2, "source_changed_at": None,
            "workspace_kind": "draft", "owner": None, "sessions": {}}


def legacy_lease(document_id):
    return {**version_one(document_id), "lease": {"token": "synthetic-token",
            "owner_id": "synthetic-user", "owner_name": "Synthetic", "client_id": "synthetic-tab",
            "acquired_at": 1, "expires_at": 2}}
