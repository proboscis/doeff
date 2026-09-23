"""Enable Hy import hook for .hy module loading in tests."""

import hy  # noqa: F401 — activates Hy import hook


import pytest


@pytest.fixture
def doeff_interpreter():
    """deftest の実行時 interpreter(ADR-DOE-HY-002 R3 の参照実装と同形)。"""

    def run_program(program, *, env=None):
        from doeff import run

        if env:
            from doeff_core_effects.handlers import reader

            program = reader(dict(env))(program)
        return run(program)

    return run_program
