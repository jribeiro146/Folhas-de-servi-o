"""Offline backup/restore; no application, credentials or transports loaded."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.release_identity import get_release_info, ReleaseIdentityError
from src.services.runtime_contract import ContractError
from src.services.file_mutex import FileMutexBusy
from src.services.runtime_migration import MigrationError, _local
from src.services.runtime_backup import backup, verify, restore, release_hold, BackupError, _manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("backup", "verify", "restore", "release"):
        command = commands.add_parser(name)
        command.add_argument("--key-file", required=True, help="Ficheiro privado separado com a FS_SECRET_KEY; nunca o próprio .env.")
        if name != "release":
            command.add_argument("--backup", required=True)
        if name != "verify":
            command.add_argument("--writers-stopped", action="store_true", required=True)
        if name == "backup":
            command.add_argument("--data-dir", required=True)
            command.add_argument("--include-root", action="append", default=[])
            command.add_argument("--roots-complete", action="store_true", required=True)
        if name in {"restore", "release"}:
            command.add_argument("--workspace", required=True)
        if name == "release":
            command.add_argument("--actor", required=True)
            command.add_argument("--reason", required=True)
            command.add_argument("--external-effects-reconciled", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        release = get_release_info(ROOT)
        if not release["verified"]:
            raise BackupError("Operação exige um artefacto com manifesto válido.")
        keyfile = _local(args.key_file)
        # Application startup strips surrounding whitespace from FS_SECRET_KEY.
        # Read only this deliberately supplied file, never parse private .env.
        key_text = keyfile.read_text(encoding="utf-8").strip()
        if len(key_text) < 32:
            raise BackupError("Chave privada incompatível: mínimo 32 caracteres.")
        key = key_text.encode("utf-8")
        workspace = _local(args.workspace) if hasattr(args, "workspace") else None
        if args.command == "release":
            plan = json.loads((workspace / "restore.json").read_text(encoding="utf-8"))
            destination = _local(plan["backup"])
        else:
            destination = _local(args.backup)
        if args.command == "backup":
            roots = [_local(args.data_dir), *map(_local, args.include_root)]
        else:
            roots = list(map(_local, _manifest(destination, key)["roots"]))
        paths = [*roots, destination, *([workspace] if workspace else [])]
        if any(path.is_relative_to(ROOT) or ROOT.is_relative_to(path) or keyfile.is_relative_to(path) for path in paths):
            raise BackupError("Dados/cópia/workspace devem ficar separados do artefacto e do ficheiro da chave.")
        common = {"source_sha256": release["source_sha256"], "key": key}
        if args.command == "backup":
            result = backup(roots[0], roots[1:], destination, **common)
        elif args.command == "verify":
            result = verify(destination, key=key)
        elif args.command == "restore":
            result = restore(destination, workspace, **common)
        else:
            result = release_hold(workspace, actor=args.actor, reason=args.reason,
                                  external_effects_reconciled=args.external_effects_reconciled, **common)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (BackupError, MigrationError, ContractError, ReleaseIdentityError, FileMutexBusy,
            OSError, sqlite3.Error, ValueError, KeyError, TypeError, RecursionError) as exc:
        print(json.dumps({"code": "backup_recovery_refused", "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
