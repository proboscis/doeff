"""Reproducer: cache() + Await + Spawn+Gather hangs at scale.

When many Spawned tasks enter cache compute_and_cache() simultaneously and each
task does a `yield Await(coroutine)` inside the cached function, the scheduler
stops dispatching new tasks after the first batch completes.

See: https://github.com/proboscis/doeff/issues/342
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any

import pytest

from doeff import (
    Gather,
    Spawn,
    WithHandler,
    cache,
    do,
    run,
    slog,)
from doeff import Await
from tests._run_helpers import run_with_defaults
# REMOVED: from doeff_core_effects.handlers import sqlite_cache_handler

TIMEOUT_SECONDS = 30


def _timeout_handler(signum: int, frame: Any) -> None:
    raise TimeoutError(f"Deadlock detected after {TIMEOUT_SECONDS}s")


def _run_with_timeout(program):
    old = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(TIMEOUT_SECONDS)
    try:
        return run_with_defaults(program)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def _run_cached_with_timeout(program):
    wrapped = WithHandler(sqlite_cache_handler(None), program)
    return _run_with_timeout(wrapped)


async def _fake_work(i: int) -> str:
    await asyncio.sleep(0.01)
    return f"done-{i}"


@do
def _no_cache_task(i: int):
    result = yield Await(_fake_work(i))
    return result


# REMOVED: @cache(lifecycle="persistent")
@do
def _cached_task(i: int):
    result = yield Await(_fake_work(i))
    return result


@do
def _spawn_gather_n(factory, n: int):
    tasks = []
    for i in range(n):
        t = yield Spawn(factory(i), daemon=False)
        tasks.append(t)
    return list((yield Gather(*tasks)))
