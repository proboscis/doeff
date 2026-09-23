"""Shared run helper for the doeff-test-target test suite.

The removed ``run(program, handlers=default_handlers(), env=...)`` surface is
replaced by composing handlers explicitly: every handler is a Program ->
Program function, so the stack is built by calling each one around the
program. Uniquely named (not ``conftest``) so it does not collide with the
other suites' helper modules when collected together.
"""

from collections.abc import Mapping
from typing import Any

from doeff_core_effects.handlers import reader, state, writer

from doeff import run


def run_with_core_handlers(program: Any, *, env: Mapping[Any, Any] | None = None) -> Any:
    """Run ``program`` under reader (``Ask``), writer (``Tell``) and the state writer needs."""

    return run(state()(writer(reader(env=env)(program))))
