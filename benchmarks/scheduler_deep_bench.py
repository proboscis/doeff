"""深い handler の下で、待つ task が多い時の scheduler の 1 操作の thread の CPU を測る(agora card ki-9de8d0a54408)。

    uv run python benchmarks/scheduler_deep_bench.py
    uv run python benchmarks/scheduler_deep_bench.py --layers 0 80 --parked 0 2000 --ops 400 --impl rust

本番の画面のサーバーの形に寄せた模擬: 本体(と Spawn した task)と scheduler の間に、ほかの effect を解く型つきの
handler を ``--layers`` 段 置き(scheduler の effect は全段を素通りする)、``--parked`` 本の daemon の task を外の約束で
待たせる(半分は WaitWithin(park=True) — 期限の timer の task も 1 本ずつ待つ、半分は素の Wait)。その上で本体が
1 操作を ``--ops`` 回する:

- ``timed_wait``: 外の約束を作って完了し、WaitWithin(park=True) で受ける(turn-host が CLI の 1 行ごとにする待ち)
- ``spawn_wait``: 何もしない task を Spawn して Wait する

1 操作の thread の CPU(time.thread_time の差 / 回数 — 中央値は repeat 回の中の値)を µs で出す。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import warnings
from typing import Any

from doeff_core_effects.handlers import await_handler
from doeff_core_effects.scheduler import (
    Cancel,
    CreateExternalPromise,
    Spawn,
    Wait,
    scheduled,
)
from doeff_time import WaitWithin, async_time_handler

from doeff import EffectBase, Resume, do, handler, run, with_handlers


class Unrelated(EffectBase[int]):
    """深い handler の段が解く effect(scheduler の effect はどの段も受けない)。"""


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


OPS = {"timed_wait": timed_wait, "spawn_wait": spawn_wait}


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

    stack = [await_handler(), async_time_handler(), *[unrelated_handler] * layers]
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
