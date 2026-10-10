"""Isolation is configured before test collection can import the web app."""

import socket
import sys

import pytest

from tools.test_runtime import configure, block_outbound_network

sys.dont_write_bytecode = True
_root = configure()
block_outbound_network()


def _deny_network(*_args, **_kwargs):
    raise AssertionError("Rede real bloqueada nos testes; injetar transporte simulado.")


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", _deny_network)
    monkeypatch.setattr(socket, "create_connection", _deny_network)


@pytest.fixture(scope="session")
def verified_test_release(tmp_path_factory):
    """An actual manifest for configuration tests which leave the synthetic mode.

    Same source bytes as this checkout; only code/tools/delivery files are copied.
    Transport mocks and network denial remain active in those tests.
    """
    from pathlib import Path
    import shutil
    from src.services.release_identity import is_delivery_file, write_release_manifest
    root = Path(__file__).resolve().parents[1]
    staging = tmp_path_factory.mktemp("verified-test-release")
    for name in ("src", "tools"):
        shutil.copytree(root / name, staging / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for path in root.iterdir():
        if is_delivery_file(path.name):
            shutil.copyfile(path, staging / path.name)
    write_release_manifest(staging, revision="a" * 40)
    return staging
