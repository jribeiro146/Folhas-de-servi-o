"""Public admission/activation contract, exercised with synthetic state only."""
import pytest
import sqlite3
import subprocess
import sys
import time


def test_only_the_active_release_can_enter_an_operation(tmp_path):
    from src.services.runtime_contract import RuntimeContract, ContractError
    first = RuntimeContract(tmp_path / "data", "a" * 64)
    first.initialize_empty()
    with first.operation() as snapshot:
        assert snapshot["source_sha256"] == "a" * 64
        assert snapshot["epoch"] == 1
    other = RuntimeContract(tmp_path / "data", "b" * 64)
    assert other.inspect()["matches_release"] is False
    with pytest.raises(ContractError, match="incompatível"):
        with other.operation():
            pytest.fail("Uma versão diferente não pode iniciar uma operação.")


def test_release_change_waits_for_operations_and_fences_old_processes(tmp_path):
    from src.services.runtime_contract import RuntimeContract, ContractError, ContractBusy
    first = RuntimeContract(tmp_path / "data", "a" * 64)
    second = RuntimeContract(tmp_path / "data", "a" * 64)
    first.initialize_empty()
    with first.operation(), second.operation():
        with pytest.raises(ContractBusy):
            first.activate("b" * 64)
    assert first.inspect()["source_sha256"] == "a" * 64
    first.activate("b" * 64)
    with pytest.raises(ContractError, match="incompatível"):
        with second.operation():
            pytest.fail("Um processo anterior não pode voltar a escrever.")
    with pytest.raises(ContractError, match="incompatível"):
        first.activate("c" * 64)
    with RuntimeContract(tmp_path / "data", "b" * 64).operation() as snapshot:
        assert snapshot["epoch"] == 2


def test_existing_unmarked_or_missing_storage_is_never_adopted(tmp_path):
    from src.services.runtime_contract import RuntimeContract, ContractError
    legacy = tmp_path / "data"
    legacy.mkdir()
    document = legacy / "old-state.json"
    document.write_text('{"synthetic":"preserve"}', encoding="utf-8")
    contract = RuntimeContract(legacy, "a" * 64)
    with pytest.raises(ContractError, match="migração"):
        contract.initialize_empty()
    with pytest.raises(ContractError):
        with contract.operation():
            pytest.fail("Dados sem preparação não autorizam operações.")
    assert document.read_text(encoding="utf-8") == '{"synthetic":"preserve"}'
    assert not contract.path.exists()


@pytest.mark.parametrize("damage", ["future-contract", "future-storage", "invalid-formats", "wal", "corrupt", "wrong-id"])
def test_invalid_or_future_contract_refuses_entry_and_activation(tmp_path, damage):
    from src.services.runtime_contract import RuntimeContract, ContractError
    contract = RuntimeContract(tmp_path / "data", "a" * 64)
    contract.initialize_empty()
    # Simulate a future/corrupt persistent format at the SQLite boundary.
    if damage == "corrupt":
        contract.path.write_bytes(b"synthetic invalid database")
    else:
        connection = sqlite3.connect(contract.path)
        try:
            if damage == "future-contract":
                connection.execute("PRAGMA user_version=99")
            elif damage == "future-storage":
                connection.execute("UPDATE state_contract SET formats_json=?", ('{"editing":99}',))
            elif damage == "invalid-formats":
                connection.execute("UPDATE state_contract SET formats_json='invalid'")
            elif damage == "wrong-id":
                connection.execute("PRAGMA ignore_check_constraints=ON")
                connection.execute("UPDATE state_contract SET singleton=2")
            else:
                connection.execute("PRAGMA journal_mode=WAL")
            connection.commit()
        finally:
            connection.close()
    with pytest.raises(ContractError):
        with contract.operation():
            pytest.fail("Contrato inválido não pode autorizar uma operação.")
    with pytest.raises(ContractError):
        contract.activate("b" * 64)


@pytest.mark.parametrize("crash", [False, True])
def test_separate_process_blocks_activation_until_exit_or_crash(tmp_path, crash):
    from src.services.runtime_contract import RuntimeContract, ContractBusy
    contract = RuntimeContract(tmp_path / "data", "a" * 64)
    contract.initialize_empty()
    ready = tmp_path / "ready"
    code = (
        "from pathlib import Path; import sys; "
        "from tools.test_runtime import block_outbound_network; block_outbound_network(); "
        "from src.services.runtime_contract import RuntimeContract; "
        "gate=RuntimeContract(Path(sys.argv[1]),'a'*64); "
        "guard=gate.operation(); guard.__enter__(); "
        "Path(sys.argv[2]).write_text('ready'); "
        "sys.stdin.read(1); guard.__exit__(None,None,None)"
    )
    process = subprocess.Popen([sys.executable, "-B", "-c", code, str(contract.data_dir), str(ready)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "O processo sintético não conseguiu entrar."
        with contract.operation():
            with pytest.raises(ContractBusy):
                contract.activate("b" * 64)
        if crash:
            process.kill()
            process.communicate(timeout=10)
        else:
            _, errors = process.communicate(input="x", timeout=10)
            assert process.returncode == 0, errors
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
    contract.activate("b" * 64)
    assert RuntimeContract(contract.data_dir, "b" * 64).inspect()["matches_release"]


def test_operation_exception_does_not_leak_the_admission_lock(tmp_path):
    from src.services.runtime_contract import RuntimeContract
    contract = RuntimeContract(tmp_path / "data", "a" * 64)
    contract.initialize_empty()
    with pytest.raises(ValueError, match="synthetic"):
        with contract.operation():
            raise ValueError("synthetic failure")
    contract.activate("b" * 64)
    assert RuntimeContract(contract.data_dir, "b" * 64).inspect()["epoch"] == 2
