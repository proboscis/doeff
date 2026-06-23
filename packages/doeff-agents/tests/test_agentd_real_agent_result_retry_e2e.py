from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import doeff_hy  # noqa: F401  # registers Hy import hooks for deftest modules
import pytest

from doeff import run

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

retry_deftests = importlib.import_module("agentd_real_agent_result_retry_e2e_deftests")


def _deftest_interpreter(program: Any, *, env: dict[Any, Any] | None = None) -> Any:
    if env is not None:
        raise ValueError("agentd real-agent result retry E2E deftests do not use env overrides")
    return run(program)


@pytest.mark.e2e
@pytest.mark.timeout(360)
def test_agentd_real_claude_result_contract_retries_invalid_output(
    tmp_path: Path,
) -> None:
    retry_deftests.test_agentd_real_claude_result_contract_retries_invalid_output(
        _deftest_interpreter,
        tmp_path,
    )


@pytest.mark.e2e
@pytest.mark.timeout(360)
def test_agentd_real_codex_result_contract_retries_invalid_output(
    tmp_path: Path,
) -> None:
    retry_deftests.test_agentd_real_codex_result_contract_retries_invalid_output(
        _deftest_interpreter,
        tmp_path,
    )
