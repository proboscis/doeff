"""Regression coverage for cache() + Await under large Spawn/Gather fan-out.

The original issue appeared when many spawned cache misses all entered a cached
``@do`` function that awaited async work. The scheduler must keep draining
external promise completions until every spawned task finishes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from doeff_core_effects._cache import cache
from doeff_core_effects import sqlite_memo_handler

from doeff import Await, Gather, Spawn, do
from tests._run_helpers import run_with_defaults


# The fake work holds its results until the program has spawned every task, so the scheduler faces all ``total``
# completions at once (the scale this guard is about) instead of taking them one by one while tasks are still being
# spawned. A result completed by ``call_soon`` arrives at once and does not keep them waiting together: a scheduler
# that drops completions once many are queued still passes it (measured), so the order is named here with a gate
# rather than left to a delay. The gate starts open, so a program that does not hold it gets its results at once.
_RESULT_GATE = asyncio.Event()
_RESULT_GATE.set()


async def _hold_results() -> None:
    _RESULT_GATE.clear()


async def _release_results() -> None:
    _RESULT_GATE.set()


async def _fake_work(index: int) -> str:
    await _RESULT_GATE.wait()
    return f"done-{index}"


@cache(lifecycle="persistent")
@do
def _cached_task(index: int) -> Any:
    result: str = yield Await(_fake_work(index))
    return result


@do
def _spawn_gather_n(factory: Callable[[int], Any], total: int) -> Any:
    yield Await(_hold_results())
    tasks: list[Any] = []
    for index in range(total):
        task: Any = yield Spawn(factory(index))
        tasks.append(task)
    yield Await(_release_results())
    return list((yield Gather(*tasks)))


def _run_cached_spawn_gather(db_path: Path, total: int) -> list[str]:
    program: Any = sqlite_memo_handler(db_path)(_spawn_gather_n(_cached_task, total))
    result: Any = run_with_defaults(program)
    if result.is_err():
        raise result.error
    return result.value


@pytest.mark.timeout(60)
@pytest.mark.parametrize("total", [200, 500])
def test_cache_await_spawn_gather_completes_at_scale(tmp_path: Path, total: int) -> None:
    values: list[str] = _run_cached_spawn_gather(tmp_path / "memo.sqlite", total)

    assert len(values) == total
    assert values == [f"done-{index}" for index in range(total)]
