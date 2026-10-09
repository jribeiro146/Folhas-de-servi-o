"""Inspect a consistent local copy only; no app/config import and no migration."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.services.editing_state_format import (
    EditingStateQuarantinedError, decode_state_object, validate_editing_state,
)


def classify(raw, document_id):
    try:
        state = decode_state_object(raw)
        validate_editing_state(state, document_id)
        return "compatible"
    except EditingStateQuarantinedError:
        pass
    try:
        state = decode_state_object(raw)
    except EditingStateQuarantinedError:
        return "invalid_state"
    if state.get("document_id") != document_id or type(state.get("version")) is not int:
        return "invalid_state"
    if state["version"] == 1:
        return "legacy_version"  # Candidate only, not proof that legacy contents are valid.
    if state["version"] != 2:
        return "unsupported_version"
    required = {"version", "document_id", "revision", "etag", "fingerprint", "aliases",
                "lease", "autosave", "operations", "source_changed_at", "workspace_kind", "owner", "sessions"}
    return "incomplete_current" if required.difference(state) else "invalid_state"


def inspect_copy(path, *, database):
    counts = Counter()
    records = []

    def record(document_id, raw):
        category = classify(raw, document_id)
        counts[category] += 1
        records.append({"document_ref": hashlib.sha256(str(document_id).encode()).hexdigest()[:16],
                        "classification": category})

    if database:
        if not path.is_file():
            raise ValueError("Copy unavailable")
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            for document_id, raw in connection.execute("SELECT document_id,state_json FROM editing_states"):
                record(document_id, raw)
    else:
        if not path.is_dir():
            raise ValueError("Copy unavailable")
        for snapshot in sorted(path.glob("*.json")):
            if snapshot.is_symlink() or not snapshot.is_file():
                raise ValueError("Unexpected snapshot")
            record(snapshot.stem, snapshot.read_bytes())
    return {"scope": "editing_aggregate_only", "total": len(records), "counts": dict(counts),
            "ready": all(row["classification"] == "compatible" for row in records), "records": records}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--database-copy", type=Path, help="Cópia consistente SQLite, incluindo o WAL aplicável.")
    sources.add_argument("--json-directory-copy", type=Path, help="Cópia da pasta de snapshots JSON antigos.")
    args = parser.parse_args(argv)
    try:
        path = args.database_copy or args.json_directory_copy
        if not path.is_absolute() or str(path).startswith(("\\\\", "//")) or path.is_symlink():
            raise ValueError("Use a local absolute copy path")
        path = path.resolve()
        if str(path).startswith(("\\\\", "//")):
            raise ValueError("Use a local copy")
        result = inspect_copy(path, database=args.database_copy is not None)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ready"] else 1
    except (OSError, ValueError, sqlite3.Error):
        print(json.dumps({"code": "inspection_failed", "error": "Não foi possível inspecionar a cópia; confirme formato e consistência."},
                         ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
