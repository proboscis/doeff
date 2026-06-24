from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import doeff_hy  # noqa: F401  # registers Hy import hooks for deftest modules

from doeff import run

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

run_tool_deftests = importlib.import_module("agent_mcp_run_tool_state_deftests")


def _deftest_interpreter(program: Any, *, env: dict[Any, Any] | None = None) -> Any:
    if env is not None:
        raise ValueError("agent MCP run_tool state deftests do not use env overrides")
    return run(program)


def test_agent_mcp_run_tool_provides_state_for_captured_lazy_handlers() -> None:
    run_tool_deftests.test_agent_mcp_run_tool_provides_state_for_captured_lazy_handlers(
        _deftest_interpreter
    )


def test_agent_mcp_run_tool_provides_await_for_captured_handler_effects() -> None:
    run_tool_deftests.test_agent_mcp_run_tool_provides_await_for_captured_handler_effects(
        _deftest_interpreter
    )
