"""Where tests/cli tests build the command that launches the child CLI (#2896).

The child process inherits the parent's environment (not copied -- the parent's environment is not read), and only the settings the test needs are added to the command line as `env NAME=VALUE ...`.
A name whose value is None is removed from the child's environment with `env -u NAME`. PATH, HOME, and uv variables reach the child by being inherited.
The VM check flag (DOEFF_VM_INVARIANT_CHECKS) is passed explicitly every time with this session's VM state (the root conftest sets it via the ini value vm_invariant_checks) --
the parent does not write it to the environment, so merely inheriting would let the child silently run without checks (ADR-DOE-ENFORCE-001 R4).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from doeff_core_effects import vm_probe_handler

from doeff import ReadInvariantChecks, run

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def with_settings(command: list[str], settings: Mapping[str, str | None]) -> list[str]:
    """Put `env ...` before the command and add only settings to the environment the child inherits (names set to None are removed)."""
    removed = [part for name, value in settings.items() if value is None for part in ("-u", name)]
    oracle = {"DOEFF_VM_INVARIANT_CHECKS": "1" if run(vm_probe_handler(ReadInvariantChecks())) else "0"}
    added = [f"{name}={value}" for name, value in {**oracle, **settings}.items() if value is not None]
    return ["env", *removed, *added, *command]
