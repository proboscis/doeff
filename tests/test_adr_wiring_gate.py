"""ADR-DOE-ENFORCE-001 R1: ADR wiring is part of the canonical pytest gate.

The default ``uv run pytest`` session has already collected the default scope,
and the doeff-adr plugin measured that collection against every executable ADR
under rootdir — this test reads that measurement (ADR-DOE-ADR-001 R3 / law
gate-test-reads-the-sessions-own-collection). It must not start a second
collection: a nested ``pytest --collect-only`` is O(suite) work under a per-test
deadline, and on the daily verify pod (no bytecode cache — the land mechanism
runs its children with PYTHONDONTWRITEBYTECODE=1 — so every collection is cold,
135 s measured 2026-09-17) it crossed the 60 s deadline three days running and
pytest-timeout's thread method took the whole run down without a summary line.
"""

from pathlib import Path

import pytest
from doeff_adr.pytest_plugin import (
    NotDefaultScope,
    WiringVerified,
    default_scope_wiring,
    wiring_failure_message,
)


def test_all_executable_adrs_are_collected_by_default_pytest_gate(
    request: pytest.FixtureRequest,
) -> None:
    verdict = default_scope_wiring(request.session)
    if isinstance(verdict, NotDefaultScope):
        # Not a green: a session aimed at chosen paths cannot speak for the
        # default scope in either direction, so it says so instead of passing.
        pytest.skip(
            f"this session collected {list(verdict.args)}, not the default scope — the "
            "canonical gate (`uv run pytest` without path arguments) verifies ADR wiring "
            "from its own collection. On demand: `uv run pytest -k "
            f"{request.node.name}` (default scope, one collection) or "
            "`uv run doeff-adr verify-wiring`."
        )
    if isinstance(verdict, WiringVerified):
        return
    pytest.fail(
        "executable ADR files must be collected by the canonical pytest gate "
        "(ADR-DOE-ENFORCE-001 R1).\n"
        + wiring_failure_message(verdict, Path(request.config.rootpath)),
        pytrace=False,
    )
