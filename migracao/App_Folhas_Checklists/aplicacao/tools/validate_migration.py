"""Run the copied application tests with synthetic state and no real network."""
from pathlib import Path
import ast
import os
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
os.chdir(ROOT)

from tools.test_runtime import configure, block_outbound_network


def main():
    runtime = configure()
    block_outbound_network()
    # Parsing never imports application/config or starts workers.
    files = [*ROOT.joinpath("src").rglob("*.py"), *ROOT.joinpath("tools").glob("*.py")]
    for file in files:
        ast.parse(file.read_text(encoding="utf-8-sig"), filename=str(file))
    if not (ROOT / "tests/fixtures/test_sample.xlsx").is_file():
        raise SystemExit("Fixture sintética ausente; executar tools/build_synthetic_fixture.py.")
    test_parent = Path(tempfile.mkdtemp(prefix="fs-migration-pytest-")).resolve()
    test_root = test_parent / "cases"
    if not test_parent.is_relative_to(Path(tempfile.gettempdir()).resolve()) or test_root.exists():
        raise SystemExit("O diretório de testes deve ser novo e exclusivo da execução.")
    print(f"Sintaxe Python: {len(files)} ficheiros. Backend local; dados temporários; rede bloqueada; envios e workers desligados.")
    import pytest
    return pytest.main(["tests", "-q", "--tb=short", "-p", "no:cacheprovider", "--basetemp", str(test_root), *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
