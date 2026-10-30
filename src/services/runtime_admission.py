"""Bind an operational factory to the code and persistent contract it uses."""
from pathlib import Path
from contextlib import contextmanager, nullcontext
from functools import wraps
import os
import threading

from src.services.release_identity import get_release_info, ReleaseIdentityError, CODE_DIRECTORIES
from src.services.runtime_contract import RuntimeContract, ContractError

_admissions = threading.local()


@contextmanager
def admit_contract(contract):
    """Reuse an outer admission only on the same thread, path and code identity."""
    entries = getattr(_admissions, "entries", None)
    if entries is None:
        entries = _admissions.entries = {}
    key = (os.getpid(), contract.path.resolve(), contract.source_sha256)
    if key in entries:
        yield entries[key]
        return
    with contract.operation() as snapshot:
        entries[key] = snapshot
        try:
            yield snapshot
        finally:
            entries.pop(key, None)


def _synthetic_profile():
    # This is an explicit test profile, never a production bypass. The test
    # launcher additionally creates fresh data and blocks the network before
    # importing the application; these flags alone do not isolate a test.
    expected = {
        "FS_ENVIRONMENT": "test", "FS_TEST_SYNTHETIC": "true",
        "FS_STORAGE_BACKEND": "local", "FS_AUTH_PROVIDER": "none",
        "FS_MAIL_ENABLED": "false", "FS_TEAMS_NOTIFICATIONS_ENABLED": "false",
        "FS_GRAPH_QUEUE_IN_WEB": "false",
    }
    return all(os.environ.get(name, "").strip().lower() == value for name, value in expected.items())


def resolve_runtime(data_dir: Path, *, release_root=None, runtime_contract=None, storage_paths=(),
                    require_manifest=True):
    root = Path(release_root) if release_root is not None else Path(__file__).resolve().parents[2]
    sources = [(root / directory).resolve() for directory in CODE_DIRECTORIES]
    paths = [data_dir, *storage_paths]
    if runtime_contract is not None:
        paths.append(runtime_contract.data_dir)
    # Never fingerprint an operational SQLite file: closing a raw descriptor can
    # drop the process's POSIX locks, even when another connection owns them.
    if any(Path(path).resolve().is_relative_to(source) for path in paths for source in sources):
        raise ContractError("As pastas de dados não podem ficar dentro do código da aplicação.")
    info = get_release_info(root)
    if require_manifest and not info["verified"] and not _synthetic_profile():
        raise ReleaseIdentityError(
            "O arranque exige um artefacto com manifesto de versão válido; "
            "gerar o pacote do commit aprovado com tools/build_release.py."
        )
    contract = runtime_contract or RuntimeContract(data_dir, info["source_sha256"])
    if contract.source_sha256 != info["source_sha256"]:
        raise ContractError("O contrato fornecido não corresponde ao código deste processo.")
    return info, contract


def operation_for(component):
    """Standalone services may omit admission; operational factories always bind it."""
    contract = getattr(component, "runtime_contract", None)
    return admit_contract(contract) if contract is not None else nullcontext()


def admitted(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with operation_for(self):
            return method(self, *args, **kwargs)
    return wrapped
