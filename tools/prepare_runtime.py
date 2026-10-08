"""Explicit offline preparation. Never import app configuration or transports."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.release_identity import ReleaseIdentityError
from src.services.runtime_admission import resolve_runtime, admit_contract
from src.services.runtime_contract import RuntimeContract, ContractError, SUPPORTED_FORMATS
from src.services.sqlite_storage_format import preflight_stores


def _data_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or value.startswith(("\\\\", "//")):
        raise ValueError("Indique uma pasta absoluta num disco local do servidor.")
    path = path.resolve()
    if str(path).startswith(("\\\\", "//")):
        raise ValueError("A pasta resolvida tem de ficar num disco local do servidor.")
    if path == Path(path.anchor):
        raise ValueError("A raiz do disco não pode ser usada como pasta de dados.")
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "init", "activate"):
        command = commands.add_parser(name)
        command.add_argument("--data-dir", required=True)
        if name == "activate":
            command.add_argument("--expected-source", required=True)
            command.add_argument("--writers-stopped", action="store_true", required=True,
                                 help="Confirma a paragem de todos os escritores, incluindo versões antigas.")
    args = parser.parse_args(argv)
    try:
        data = _data_path(args.data_dir)
        info, contract = resolve_runtime(data, release_root=ROOT)
        if args.command != "inspect" and not info["verified"]:
            raise ContractError("A preparação exige um artefacto com manifesto de versão válido.")
        if args.command == "init":
            contract.initialize_empty()
            with admit_contract(contract) as snapshot:
                preflight_stores(data)
        elif args.command == "activate":
            previous = RuntimeContract(data, args.expected_source)
            snapshot = previous.activate(info["source_sha256"], next_formats=SUPPORTED_FORMATS,
                                         validate_storage=lambda: preflight_stores(data))
        else:
            # Diagnostic identity only: this does not certify storage, credentials
            # or operational readiness, and it can inspect a different release.
            snapshot = contract.inspect()
        print(json.dumps({"command": args.command, "release": info, "runtime": snapshot}, sort_keys=True))
        return 0
    except (ContractError, ReleaseIdentityError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc), "code": getattr(exc, "code", "preparation_failed")}),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
