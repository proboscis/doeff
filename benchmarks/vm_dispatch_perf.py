"""VM dispatch micro-benchmark.

Usage:
    .venv/bin/python benchmarks/vm_dispatch_perf.py
    DOEFF_VM_BENCH_DISPATCHES=10000 .venv/bin/python benchmarks/vm_dispatch_perf.py
"""

from __future__ import annotations

import os
import statistics
import time

import doeff_vm

from doeff.do import do
from doeff.effects.reader import Ask
from doeff.rust_vm import default_handlers, run


def _median_us_per_dispatch(samples: list[float]) -> float:
    return statistics.median(samples)


def _generator_baseline_us(dispatches: int, runs: int) -> float:
    def gen():
        while True:
            yield

    samples: list[float] = []
    for _ in range(runs):
        g = gen()
        next(g)
        start = time.perf_counter()
        for _ in range(dispatches):
            g.send(1)
        elapsed = time.perf_counter() - start
        samples.append(elapsed * 1_000_000.0 / dispatches)
    return _median_us_per_dispatch(samples)


def _reader_program(dispatches: int):
    @do
    def program() -> int:
        total = 0
        for _ in range(dispatches):
            total += yield Ask("bench_key")
        return total

    return program


def _run_dispatch_us(dispatches: int, handlers: list[object], runs: int) -> float:
    program = _reader_program(dispatches)
    env = {"bench_key": 1}

    samples: list[float] = []
    for _ in range(runs):
        start = time.perf_counter()
        result = run(program(), handlers=handlers, env=env, print_doeff_trace=False)
        elapsed = time.perf_counter() - start
        if result.is_err():
            raise RuntimeError(result.error)
        samples.append(elapsed * 1_000_000.0 / dispatches)
    return _median_us_per_dispatch(samples)


def main() -> None:
    dispatches = int(os.getenv("DOEFF_VM_BENCH_DISPATCHES", "10000"))
    runs = int(os.getenv("DOEFF_VM_BENCH_RUNS", "10"))

    baseline = _generator_baseline_us(dispatches, runs)
    reader_only = _run_dispatch_us(dispatches, [doeff_vm.reader], runs)
    defaults = _run_dispatch_us(dispatches, default_handlers(), runs)

    print(f"dispatches={dispatches} runs={runs}")
    print(f"generator_send_yield_us={baseline:.3f}")
    print(f"vm_reader_only_us={reader_only:.3f}")
    print(f"vm_default_handlers_us={defaults:.3f}")


if __name__ == "__main__":
    main()
