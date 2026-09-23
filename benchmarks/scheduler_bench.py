"""scheduler の代表的な流れの費用を測る(1 操作あたりの µs)。

    uv run python benchmarks/scheduler_bench.py            # 既定の scheduler
    uv run python benchmarks/scheduler_bench.py --impl python
    uv run python benchmarks/scheduler_bench.py --impl rust
    uv run python benchmarks/scheduler_bench.py --only spawn_wait --n 20000 --repeat 5

各流れは「何を 1 操作と数えるか」を名前の横に書く。最良値(repeat 回の最小)を出す。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from typing import Any

from doeff_core_effects.scheduler import (
    AcquireSemaphore,
    Cancel,
    CompletePromise,
    CreatePromise,
    CreateSemaphore,
    Gather,
    Race,
    ReleaseSemaphore,
    SchedulerImplementation,
    Spawn,
    TaskCancelledError,
    Wait,
    resolve_implementation,
    scheduled,
)

from doeff import EffectBase, Resume, do, run, with_handlers


class Outside(EffectBase[int]):
    """scheduler の外側の handler が解く effect(scheduler を素通りする費用)。"""


@do
def outside_handler(effect: Outside, k: Any):
    return (yield Resume(k, 1))


@do
def noop():
    return 1
    yield  # a generator: @do takes a generator function


@do
def spawn_wait(n: int):
    total = 0
    for _ in range(n):
        t = yield Spawn(noop())
        total += yield Wait(t)
    return total


@do
def gather(n: int):
    tasks = []
    for _ in range(n):
        tasks.append((yield Spawn(noop())))
    results = yield Gather(*tasks)
    return len(results)


@do
def race(n: int):
    total = 0
    for _ in range(n):
        a = yield Spawn(noop())
        b = yield Spawn(noop())
        total += yield Race(a, b)
    return total


@do
def semaphore_uncontended(n: int):
    sem = yield CreateSemaphore(1)
    for _ in range(n):
        yield AcquireSemaphore(sem)
        yield ReleaseSemaphore(sem)
    return n


@do
def _sem_worker(sem: Any, n: int):
    for _ in range(n):
        yield AcquireSemaphore(sem)
        t = yield Spawn(noop())
        yield Wait(t)
        yield ReleaseSemaphore(sem)
    return n


@do
def semaphore_contended(n: int):
    sem = yield CreateSemaphore(1)
    workers = 4
    tasks = []
    for _ in range(workers):
        tasks.append((yield Spawn(_sem_worker(sem, n // workers))))
    yield Gather(*tasks)
    return n


@do
def _complete(p: Any):
    yield CompletePromise(p, 1)


@do
def promise(n: int):
    total = 0
    for _ in range(n):
        p = yield CreatePromise()
        yield Spawn(_complete(p))
        total += yield Wait(p.future)
    return total


@do
def _parked(p: Any, log: list[str]):
    try:
        yield Wait(p.future)
    finally:
        log.append("finally")


@do
def cancel(n: int):
    log: list[str] = []
    for _ in range(n):
        p = yield CreatePromise()
        t = yield Spawn(_parked(p, log))
        started = yield Spawn(noop())
        yield Wait(started)
        yield Cancel(t)
        try:
            yield Wait(t)
        except TaskCancelledError:
            log.append("cancelled")
    assert log.count("finally") == n
    return n


@do
def pass_through(n: int):
    total = 0
    for _ in range(n):
        total += yield Outside()
    return total


FLOWS: dict[str, tuple[str, Callable[[int], Any], bool]] = {
    "spawn_wait": ("Spawn + Wait 1 往復", spawn_wait, False),
    "gather": ("Gather の子 1 つ(Spawn を含む)", gather, False),
    "race": ("Race 1 回(Spawn 2 回を含む)", race, False),
    "semaphore_uncontended": ("Acquire + Release 1 組(競合なし)", semaphore_uncontended, False),
    "semaphore_contended": (
        "Acquire + Spawn + Wait + Release(4 task で競合)",
        semaphore_contended,
        False,
    ),
    "promise": ("CreatePromise + Spawn(Complete) + Wait", promise, False),
    "cancel": ("Spawn + Cancel + finally + Wait(取り消し)", cancel, False),
    "pass_through": ("scheduler を素通りする effect 1 回", pass_through, True),
}


def run_flow(
    make: Callable[[int], Any],
    n: int,
    outside: bool,
    implementation: SchedulerImplementation | None = None,
) -> float:
    program = scheduled(make(n), implementation=implementation)
    if outside:
        program = with_handlers([outside_handler], program)
    started = time.perf_counter()
    run(program)
    return time.perf_counter() - started


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=20_000)
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--only", action="append")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--impl", choices=["python", "rust"], default=None)
    args = parser.parse_args()
    implementation: SchedulerImplementation = resolve_implementation(args.impl)
    results = {}
    for name, (label, make, outside) in FLOWS.items():
        if args.only and name not in args.only:
            continue
        best = min(run_flow(make, args.n, outside, implementation) for _ in range(args.repeat))
        us = best / args.n * 1e6
        results[name] = us
        if not args.json:
            print(f"{name:24s} {us:8.3f} us  {label}")
    if args.json:
        json.dump({"impl": implementation, "n": args.n, "us_per_op": results}, sys.stdout)
        print()


if __name__ == "__main__":
    main()
