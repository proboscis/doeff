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

launch_deftests = importlib.import_module("agent_launch_invariant_deftests")
await_result_deftests = importlib.import_module("agent_await_result_deftests")


def _deftest_interpreter(program: Any, *, env: dict[Any, Any] | None = None) -> Any:
    if env is not None:
        raise ValueError("agent launch invariant deftests do not use env overrides")
    return run(program)


def test_claude_adapter_launches_interactive_terminal_session() -> None:
    launch_deftests.test_claude_adapter_launches_interactive_terminal_session(
        _deftest_interpreter
    )


def test_codex_adapter_launches_interactive_terminal_session() -> None:
    launch_deftests.test_codex_adapter_launches_interactive_terminal_session(
        _deftest_interpreter
    )


def test_await_result_reobserves_transient_awaiting_input(tmp_path: Path) -> None:
    await_result_deftests.test_await_result_reobserves_transient_awaiting_input(
        _deftest_interpreter,
        tmp_path,
    )


def test_await_result_returns_stable_awaiting_input(tmp_path: Path) -> None:
    await_result_deftests.test_await_result_returns_stable_awaiting_input(
        _deftest_interpreter,
        tmp_path,
    )
