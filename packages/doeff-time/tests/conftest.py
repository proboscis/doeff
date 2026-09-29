import sys
from collections.abc import Callable
from pathlib import Path

import hy  # noqa: F401  - lets the Hy composition module (time_contract_clocks.hy) be imported
import pytest
from doeff_adr.pytest_plugin import DoeffAdrHyFile

from doeff import Program

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parents[2]
TIME_PACKAGE_ROOT = ROOT / "packages" / "doeff-time" / "src"
EVENTS_PACKAGE_ROOT = ROOT / "packages" / "doeff-events" / "src"

# TESTS_DIR makes the uniquely-named `time_test_support` helper module
# importable even when this suite is collected together with other
# testpaths (pytest only prepends a test file's own directory lazily).
for package_root in (TIME_PACKAGE_ROOT, EVENTS_PACKAGE_ROOT, TESTS_DIR):
    if str(package_root) not in sys.path:
        sys.path.insert(0, str(package_root))


# The contract tests are Hy `test_*.hy` files (deftest). doeff-adr's Hy file
# collector is used only inside this directory — the same shape as
# packages/doeff-cluster/tests/conftest.py (the root ini's
# `doeff_adr_hy_files` is left alone).
def pytest_collect_file(file_path: Path, parent: pytest.Collector) -> pytest.Collector | None:
    if file_path.suffix == ".hy" and file_path.name.startswith("test_"):
        return DoeffAdrHyFile.from_parent(parent, path=file_path)
    return None


@pytest.fixture
def doeff_interpreter_name() -> str:
    return "sim"


@pytest.fixture
def doeff_interpreter(doeff_interpreter_name: str) -> Callable[[Program], object]:
    """Runs a deftest's Program under the clock handler named by `:interpreters`
    (the composition is time_contract_clocks.hy; agora-redesign #1159)."""
    from time_contract_clocks import under_clock
    from time_test_support import run_with_handlers

    def interpret(program: Program) -> object:
        return run_with_handlers(under_clock(doeff_interpreter_name, program))

    return interpret
