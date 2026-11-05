"""Measure the thread CPU of one scheduler operation under deep handlers when many tasks are waiting.

    uv run python benchmarks/scheduler_deep_bench.py
    uv run python benchmarks/scheduler_deep_bench.py --layers 0 80 --parked 0 2000 --ops 400 --impl rust

A simulation close to the shape of the production screen server: between the body (and Spawned tasks) and the scheduler, place ``--layers`` layers of typed
handlers that solve other effects (the scheduler's effects pass straight through every layer), and make ``--parked`` daemon tasks wait on external promises
(half with WaitWithin(park=True) -- each also waits for a deadline-timer task, half with a plain Wait). On top of that the body performs
one operation ``--ops`` times:

- ``timed_wait``: create an external promise and complete it, receiving with WaitWithin(park=True) (the wait turn-host does for each CLI line)
- ``blocking_wait``: wait for an external promise without park, with the scheduler stopped, receiving the completion from another thread
  (time while stopped is not counted in thread CPU)
- ``spawn_wait``: Spawn a task that does nothing and Wait for it

Prints the thread CPU of one operation (difference of time.thread_time / count -- the median is the value within repeat runs) in µs.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
import warnings
from typing import Any

from doeff_core_effects import Cancel, CreateExternalPromise, Spawn, Wait, await_handler, scheduled
from doeff_time import WaitWithin, async_time_handler

from doeff import EffectBase, Resume, do, handler, run, with_handlers


class Unrelated(EffectBase[int]):
    """An effect solved by a layer of the deep handler (the scheduler's effects are not received by any layer)."""


@handler
def unrelated_handler(effect: Unrelated, k: Any):
    return (yield Resume(k, 1))


@do
def noop():
    return 1
    yield  # a generator: @do takes a generator function


@do
def parked_timed():
    bell = yield CreateExternalPromise()
    yield WaitWithin(bell.future, 3600.0, park=True)


@do
def parked_plain():
    bell = yield CreateExternalPromise()
    yield Wait(bell.future)


@do
def timed_wait():
    bell = yield CreateExternalPromise()
    bell.complete(True)
    return (yield WaitWithin(bell.future, 3600.0, park=True))


@do
def spawn_wait():
    return (yield Wait((yield Spawn(noop()))))


@do
def blocking_wait():
    bell = yield CreateExternalPromise()
    threading.Timer(0.0005, bell.complete, (True,)).start()
    return (yield Wait(bell.future))


OPS = {"timed_wait": timed_wait, "blocking_wait": blocking_wait, "spawn_wait": spawn_wait}


def measure(op: str, layers: int, parked: int, ops: int, repeat: int, impl: str) -> tuple[float, ...]:
    """µs of thread CPU per op, one value per repeat."""

    @do
    def body():
        tasks: tuple[Any, ...] = ()
        for i in range(parked):
            tasks = (*tasks, (yield Spawn(parked_timed() if i % 2 == 0 else parked_plain(), daemon=True)))
        for _ in range(4):  # warm up: the parked tasks start and park
            yield OPS[op]()
        per_op: tuple[float, ...] = ()
        for _ in range(repeat):
            start = time.thread_time()
            for _ in range(ops):
                yield OPS[op]()
            per_op = (*per_op, (time.thread_time() - start) / ops * 1e6)
        for task in tasks:
            yield Cancel(task)
        return per_op

    stack = [await_handler, async_time_handler(), *[unrelated_handler] * layers]
    return run(scheduled(with_handlers(stack, body()), implementation=impl))


def main(argv: list[str]) -> None:
    # The parked deadline timers are cancelled before their sleep coroutine is awaited.
    warnings.filterwarnings("ignore", "coroutine .sleep. was never awaited", RuntimeWarning)
    parser = argparse.ArgumentParser()
    parser.add_argument("--layers", type=int, nargs="+", default=[0, 80])
    parser.add_argument("--parked", type=int, nargs="+", default=[0, 2000])
    parser.add_argument("--ops", type=int, default=200)
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--only", choices=sorted(OPS), nargs="*")
    parser.add_argument("--impl", default="rust")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    rows: tuple[dict[str, object], ...] = ()
    for op in args.only or sorted(OPS):
        for layers in args.layers:
            for parked in args.parked:
                values = measure(op, layers, parked, args.ops, args.repeat, args.impl)
                row = {
                    "op": op, "layers": layers, "parked": parked,
                    "median_us": round(statistics.median(values), 1), "min_us": round(min(values), 1),
                }
                rows = (*rows, row)
                if not args.json:
                    print(f"{op:11s} layers={layers:3d} parked={parked:5d}  "
                          f"median {row['median_us']:9.1f} µs  min {row['min_us']:9.1f} µs", flush=True)
    if args.json:
        print(json.dumps(rows))


if __name__ == "__main__":
    main(sys.argv[1:])
