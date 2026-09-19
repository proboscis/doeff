"""ADR-DOE-ENFORCE-001 R1 / R8: collection wiring is part of the canonical pytest gate.

Two file kinds are checked by one measurement — executable ADRs (R2) and
ordinary Python test files (R8) — because they are the same hole twice: a file
that exists, looks like a check, and is never reached by ``uv run pytest``.
The Python side is the worse of the two, because the tests *look* written:
2026-09-19 found 19 of the 27 ``packages/*/tests`` trees plus the doeff-agents
conformance suite outside ``testpaths``, 3,200 tests silent, and five files
rotted against APIs deleted months earlier without a single red.

The default ``uv run pytest`` session has already collected the default scope,
and the doeff-adr plugin measured that collection against every owned file
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


def test_everything_the_canonical_gate_owns_is_collected(
    request: pytest.FixtureRequest,
) -> None:
    verdict = default_scope_wiring(request.session)
    if isinstance(verdict, NotDefaultScope):
        # Not a green: a session aimed at chosen paths cannot speak for the
        # default scope in either direction, so it says so instead of passing.
        pytest.skip(
            f"this session collected {list(verdict.args)}, not the default scope — the "
            "canonical gate (`uv run pytest` without path arguments) verifies collection "
            "wiring from its own collection. On demand: `uv run pytest -k "
            f"{request.node.name}` (default scope, one collection) or "
            "`uv run doeff-adr verify-wiring`."
        )
    if isinstance(verdict, WiringVerified):
        return
    pytest.fail(
        "every executable ADR and every Python test file must be collected by the "
        "canonical pytest gate (ADR-DOE-ENFORCE-001 R1 / R8).\n"
        + wiring_failure_message(verdict, Path(request.config.rootpath)),
        pytrace=False,
    )
