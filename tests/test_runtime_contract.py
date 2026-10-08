"""Public admission/activation contract, exercised with synthetic state only."""
import pytest
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
import json

FORMATS = {"editing": 1, "queue": 1, "bundle": 1, "sadi": 1, "inventory": 1}


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
            first.activate("b" * 64, next_formats=FORMATS)
    assert first.inspect()["source_sha256"] == "a" * 64
    first.activate("b" * 64, next_formats=FORMATS)
    with pytest.raises(ContractError, match="incompatível"):
        with second.operation():
            pytest.fail("Um processo anterior não pode voltar a escrever.")
    with pytest.raises(ContractError, match="incompatível"):
        first.activate("c" * 64, next_formats=FORMATS)
    with RuntimeContract(tmp_path / "data", "b" * 64).operation() as snapshot:
        assert snapshot["epoch"] == 2


def test_existing_unmarked_or_missing_storage_is_never_adopted(tmp_path):
    from src.services.runtime_contract import RuntimeContract, ContractError
    legacy = tmp_path / "data"
    legacy.mkdir()
    document = legacy / "old-state.json"
    document.write_text('{"synthetic":"preserve"}', encoding="utf-8")
    before = set(legacy.iterdir())
    contract = RuntimeContract(legacy, "a" * 64)
    with pytest.raises(ContractError, match="migração"):
        contract.initialize_empty()
    with pytest.raises(ContractError):
        with contract.operation():
            pytest.fail("Dados sem preparação não autorizam operações.")
    assert document.read_text(encoding="utf-8") == '{"synthetic":"preserve"}'
    assert not contract.path.exists()
    assert set(legacy.iterdir()) == before


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
        contract.activate("b" * 64, next_formats=FORMATS)


@pytest.mark.parametrize("crash", [False, True])
def test_separate_process_blocks_activation_until_exit_or_crash(tmp_path, crash, monkeypatch):
    from src.services.runtime_contract import RuntimeContract, ContractBusy
    contract = RuntimeContract(tmp_path / "data", "a" * 64)
    contract.initialize_empty()
    ready = tmp_path / "ready"
    monkeypatch.chdir(tmp_path)
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
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               cwd=Path(__file__).resolve().parents[1])
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        if not ready.exists():
            if process.poll() is None:
                process.kill()
            _, errors = process.communicate(timeout=10)
            pytest.fail("O processo sintético não conseguiu entrar: " + errors)
        with contract.operation():
            with pytest.raises(ContractBusy):
                contract.activate("b" * 64, next_formats=FORMATS)
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
    contract.activate("b" * 64, next_formats=FORMATS)
    assert RuntimeContract(contract.data_dir, "b" * 64).inspect()["matches_release"]


def test_operation_exception_does_not_leak_the_admission_lock(tmp_path):
    from src.services.runtime_contract import RuntimeContract
    contract = RuntimeContract(tmp_path / "data", "a" * 64)
    contract.initialize_empty()
    with pytest.raises(ValueError, match="synthetic"):
        with contract.operation():
            raise ValueError("synthetic failure")
    contract.activate("b" * 64, next_formats=FORMATS)
    assert RuntimeContract(contract.data_dir, "b" * 64).inspect()["epoch"] == 2


def test_incompatible_formats_remain_visible_for_diagnosis(tmp_path):
    from src.services.runtime_contract import RuntimeContract, ContractError
    contract = RuntimeContract(tmp_path / "data", "a" * 64)
    contract.initialize_empty()
    connection = sqlite3.connect(contract.path)
    try:
        connection.execute("UPDATE state_contract SET formats_json=?", ('{"future_store":2}',))
        connection.commit()
    finally:
        connection.close()
    snapshot = contract.inspect()
    assert snapshot["source_sha256"] == "a" * 64
    assert snapshot["formats"] == {"future_store": 2}
    assert snapshot["formats_compatible"] is False
    with pytest.raises(ContractError):
        with contract.operation():
            pytest.fail("O diagnóstico não autoriza entrada com formato futuro.")


def test_sequential_thread_handoff_closes_the_operation(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from src.services.runtime_contract import RuntimeContract
    contract = RuntimeContract(tmp_path / "data", "a" * 64)
    contract.initialize_empty()
    guard = contract.operation()
    guard.__enter__()
    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(guard.__exit__, None, None, None).result(timeout=5)
    contract.activate("b" * 64, next_formats=FORMATS)
    assert RuntimeContract(contract.data_dir, "b" * 64).inspect()["matches_release"]


def test_activation_requires_the_target_storage_formats(tmp_path):
    from src.services.runtime_contract import RuntimeContract, ContractError
    contract = RuntimeContract(tmp_path / "data", "a" * 64)
    contract.initialize_empty()
    with pytest.raises(ContractError, match="formato"):
        contract.activate("b" * 64, next_formats={"queue": 99})
    assert contract.inspect()["source_sha256"] == "a" * 64


def test_exclusive_transition_blocks_entry_and_recovers_its_hot_journal_after_crash(tmp_path):
    from src.services.runtime_contract import RuntimeContract, ContractBusy
    contract = RuntimeContract(tmp_path / "data", "a" * 64)
    contract.initialize_empty()
    ready = tmp_path / "ready"
    # SQLite boundary fixture: interrupt a real exclusive update before commit.
    # Force pages to spill so this exercises recovery, not only an in-memory rollback.
    code = '''
from pathlib import Path
import sqlite3, sys
connection = sqlite3.connect(sys.argv[1], isolation_level=None)
connection.execute("PRAGMA cache_size=1")
connection.execute("BEGIN EXCLUSIVE")
connection.execute("UPDATE state_contract SET source_sha256=?, epoch=2", ("b"*64,))
connection.execute("CREATE TABLE interrupted_state(payload BLOB)")
connection.execute("INSERT INTO interrupted_state VALUES(randomblob(262144))")
Path(sys.argv[2]).write_text("ready")
sys.stdin.read(1)
'''
    process = subprocess.Popen([sys.executable, "-B", "-c", code, str(contract.path), str(ready)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               cwd=Path(__file__).resolve().parents[1])
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        if not ready.exists():
            if process.poll() is None:
                process.kill()
            _, errors = process.communicate(timeout=10)
            pytest.fail(errors or "O processo sintético não iniciou a transação.")
        with pytest.raises(ContractBusy):
            with contract.operation():
                pytest.fail("Não se pode entrar durante a troca exclusiva.")
        journal = contract.path.with_name(contract.path.name + "-journal")
        assert journal.stat().st_size > 512
        with journal.open("rb") as stream:
            assert stream.read(8) != b"\0" * 8
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)
    with contract.operation() as snapshot:
        assert snapshot["source_sha256"] == "a" * 64
        assert snapshot["epoch"] == 1


def test_two_processes_can_initialize_once_without_abandoned_temporaries(tmp_path):
    from src.services.runtime_contract import RuntimeContract
    root = tmp_path / "data"
    start = tmp_path / "start"
    code = '''
from pathlib import Path
import json, sys, time
from tools.test_runtime import block_outbound_network
block_outbound_network()
from src.services.runtime_contract import RuntimeContract, ContractBusy
deadline = time.monotonic()+10
while not Path(sys.argv[2]).exists():
    if time.monotonic()>deadline: raise RuntimeError("synthetic start timeout")
    time.sleep(0.01)
try:
    print(json.dumps(RuntimeContract(Path(sys.argv[1]),"a"*64).initialize_empty()))
except ContractBusy:
    print(json.dumps({"busy":True}))
'''
    children = [subprocess.Popen([sys.executable, "-B", "-c", code, str(root), str(start)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                cwd=Path(__file__).resolve().parents[1]) for _ in range(2)]
    try:
        start.write_text("start", encoding="utf-8")
        results = []
        for child in children:
            output, errors = child.communicate(timeout=15)
            assert child.returncode == 0, errors
            results.append(json.loads(output))
        assert any(result.get("epoch") == 1 for result in results)
        assert all(result.get("epoch") == 1 or result.get("busy") for result in results)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=10)
    assert RuntimeContract(root, "a" * 64).inspect()["epoch"] == 1
    assert not list(root.glob(".runtime-contract-*.tmp"))


def test_initialize_repeatedly_preserves_contract_and_rejects_a_different_release(tmp_path):
    from src.services.runtime_contract import RuntimeContract, ContractError
    contract = RuntimeContract(tmp_path / "data", "a" * 64)
    before = contract.initialize_empty()
    assert contract.initialize_empty() == before
    with pytest.raises(ContractError, match="incompatível"):
        RuntimeContract(contract.data_dir, "b" * 64).initialize_empty()
    assert contract.inspect() == before
