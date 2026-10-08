"""Bind an operational factory to the code and persistent contract it uses."""
from pathlib import Path
from contextlib import nullcontext
from functools import wraps

from src.services.release_identity import get_release_info
from src.services.runtime_contract import RuntimeContract, ContractError


def resolve_runtime(data_dir: Path, *, release_root=None, runtime_contract=None, storage_paths=()):
    root = Path(release_root) if release_root is not None else Path(__file__).resolve().parents[2]
    source = (root / "src").resolve()
    paths = [data_dir, *storage_paths]
    if runtime_contract is not None:
        paths.append(runtime_contract.data_dir)
    # Never fingerprint an operational SQLite file: closing a raw descriptor can
    # drop the process's POSIX locks, even when another connection owns them.
    if any(Path(path).resolve().is_relative_to(source) for path in paths):
        raise ContractError("As pastas de dados não podem ficar dentro do código da aplicação.")
    info = get_release_info(root)
    contract = runtime_contract or RuntimeContract(data_dir, info["source_sha256"])
    if contract.source_sha256 != info["source_sha256"]:
        raise ContractError("O contrato fornecido não corresponde ao código deste processo.")
    return info, contract


def operation_for(component):
    """Standalone services may omit admission; operational factories always bind it."""
    contract = getattr(component, "runtime_contract", None)
    return contract.operation() if contract is not None else nullcontext()


def admitted(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with operation_for(self):
            return method(self, *args, **kwargs)
    return wrapped
