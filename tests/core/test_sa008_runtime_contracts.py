"""SA-008 runtime contract regressions (converted from ad-hoc probes).

These tests codify correctness expectations for Rust VM run/store behavior.
"""

from __future__ import annotations

import asyncio

from doeff import (
    Await,
    do,
)
from tests._run_helpers import run_with_defaults


def test_sa008_sync_await_runs_in_default_handlers() -> None:
    @do
    def prog():
        # 主張は「既定の handler で coroutine を 1 つ待てる」— loop に 1 度譲る coroutine で足りる(実時間は待たない・#2957)
        _ = yield Await(asyncio.sleep(0))
        return "ok"

    result = run_with_defaults(prog())

    assert result.value == "ok"


def test_sa008_sync_await_propagates_coroutine_error() -> None:
    async def boom() -> None:
        raise ValueError("await boom")

    @do
    def prog():
        _ = yield Await(boom())
        return "unreachable"

    result = run_with_defaults(prog())

    assert result.is_err()
    assert isinstance(result.error, ValueError)
    assert str(result.error) == "await boom"
