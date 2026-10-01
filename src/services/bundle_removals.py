"""Explicit local removals and receipts for immutable upload snapshots."""
import json
import os
import secrets
from pathlib import Path

REMOVALS_NAME = ".fs-removals.json"


def read_removals(directory: Path) -> dict:
    path = directory / REMOVALS_NAME
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("files"), dict):
        raise ValueError("Registo de remoções inválido; requer revisão.")
    for name, record in value["files"].items():
        parts = name.split("/")
        if (not name or any(p in {"", ".", ".."} or ":" in p or "\\" in p for p in parts)
                or not isinstance(record, dict) or not isinstance(record.get("token"), str)):
            raise ValueError("Registo de remoções inválido; requer revisão.")
    return value["files"]


def write_removals(directory: Path, records: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / REMOVALS_NAME
    temporary = path.with_name(path.name + "." + secrets.token_hex(6) + ".tmp")
    try:
        temporary.write_text(json.dumps({"version": 1, "files": records}), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def metadata(file: Path) -> dict:
    path = file.with_name(file.name + ".graph.json")
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Metadados da remoção inválidos; requer revisão.")
    return value


def record_removal(directory: Path, file: Path) -> None:
    records = read_removals(directory)
    name = file.relative_to(directory).as_posix()
    if name in records:
        return  # Preserve the baseline until confirmed; do not adopt a new eTag.
    item = metadata(file)
    if not file.exists() and not item:
        return
    records[name] = {"token": secrets.token_hex(16), "id": str(item.get("id") or ""),
                     "eTag": str(item.get("eTag") or ""), "confirmed": bool(item.get("deleted"))}
    write_removals(directory, records)


def cancel_removal(directory: Path, file: Path) -> None:
    records = read_removals(directory)
    previous = records.pop(file.relative_to(directory).as_posix(), None)
    if previous is not None:
        # Cache refresh may have removed orphan metadata after a deletion. Keep
        # its receipt when recreating, so upload uses conditional creation.
        meta_path = file.with_name(file.name + ".graph.json")
        if previous.get("confirmed") and not meta_path.exists():
            temporary = meta_path.with_name(meta_path.name + "." + secrets.token_hex(6) + ".tmp")
            try:
                temporary.write_text(json.dumps({"id": previous.get("id", ""),
                    "eTag": previous.get("eTag", ""), "deleted": True}), encoding="utf-8")
                os.replace(temporary, meta_path)
            finally:
                temporary.unlink(missing_ok=True)
        write_removals(directory, records)


def copy_confirmations(source: Path, destination: Path) -> None:
    """Acknowledge only the same intent; a newer remove/recreate is preserved."""
    source_records = read_removals(source)
    target_records = read_removals(destination)
    changed = False
    for name, record in source_records.items():
        target = target_records.get(name)
        if record.get("confirmed") and target and target.get("token") == record.get("token"):
            if not target.get("confirmed"):
                target["confirmed"] = True
                changed = True
    if changed:
        write_removals(destination, target_records)


def refresh_own_removal_versions(directory: Path) -> None:
    """Follow preceding confirmed own uploads, never an arbitrary remote read."""
    records = read_removals(directory)
    changed = False
    for name, record in records.items():
        if record.get("confirmed") or not record.get("id"):
            continue
        item = metadata(directory / name)
        if (item.get("local_publication") and not item.get("deleted")
                and item.get("id") == record["id"] and item.get("eTag")
                and item["eTag"] != record.get("eTag")):
            record["eTag"] = item["eTag"]
            changed = True
    if changed:
        write_removals(directory, records)
