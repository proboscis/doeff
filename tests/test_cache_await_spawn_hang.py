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
from doeff_core_effects.cache import cache
from doeff_core_effects.memo_handlers import sqlite_memo_handler

from doeff import Await, Gather, Spawn, WithHandler, do
from tests._run_helpers import run_with_defaults


async def _fake_work(index: int) -> str:
    await asyncio.sleep(0.01)
    return f"done-{index}"


@cache(lifecycle="persistent")
@do
def _cached_task(index: int) -> Any:
    result: str = yield Await(_fake_work(index))
    return result


@do
def _spawn_gather_n(factory: Callable[[int], Any], total: int) -> Any:
    tasks: list[Any] = []
    for index in range(total):
        task: Any = yield Spawn(factory(index))
        tasks.append(task)
    return list((yield Gather(*tasks)))


def _run_cached_spawn_gather(db_path: Path, total: int) -> list[str]:
    program: Any = WithHandler(sqlite_memo_handler(db_path), _spawn_gather_n(_cached_task, total))
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
