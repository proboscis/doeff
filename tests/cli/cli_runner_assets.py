"""Test-only runners used by tests/cli/test_cli_runner.py."""

from __future__ import annotations

import dataclasses
import json

NOT_A_RUNNER = 42


def ctx_spy_runner(ctx) -> int:
    """Serialize the RunContext to stdout and return 0 without executing."""
    payload = dataclasses.asdict(ctx)
    print(json.dumps(payload))
    return 0
