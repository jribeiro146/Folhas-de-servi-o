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
