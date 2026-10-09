"""Offline migration of explicitly inventoried storage; never imports the app."""
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
from src.services.runtime_migration import prepare, apply, MigrationError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "apply"):
        command = commands.add_parser(name)
        command.add_argument("--workspace", required=True)
        command.add_argument("--writers-stopped", action="store_true", required=True,
                             help="Web, worker, tarefas agendadas e versões antigas estão parados.")
        if name == "prepare":
            command.add_argument("--data-dir", required=True)
            command.add_argument("--include-root", action="append", default=[])
            command.add_argument("--roots-complete", action="store_true", required=True,
                                 help="Incluídos Excel/cache externos, SADI, snapshots, anexos e recibos.")
    args = parser.parse_args(argv)
    try:
        release = get_release_info(ROOT)
        if not release["verified"]:
            raise MigrationError("A migração exige um artefacto com manifesto válido.")
        if args.command == "prepare":
            paths = [args.data_dir, *args.include_root, args.workspace]
            if any(Path(p).resolve().is_relative_to(ROOT.resolve()) for p in paths):
                raise MigrationError("Dados e workspace têm de ficar fora do artefacto de código.")
            result = prepare(Path(args.data_dir), list(map(Path, args.include_root)), Path(args.workspace),
                             source_sha256=release["source_sha256"])
        else:
            result = apply(Path(args.workspace), source_sha256=release["source_sha256"])
        print(json.dumps(result, sort_keys=True))
        return 0
    except (MigrationError, ContractError, ReleaseIdentityError, OSError, sqlite3.Error,
            FileMutexBusy, ValueError) as exc:
        print(json.dumps({"code": "migration_refused", "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
