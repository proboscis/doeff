"""Micro-benchmarks for the doeff interpreter.

Usage
-----
    uv run python benchmarks/benchmark_runner.py --runs 500
"""

from __future__ import annotations

import argparse
import statistics
import time
from typing import Iterable

from doeff import ExecutionContext, ProgramInterpreter, do
from doeff.effects import Ask, Log, Put


@do
def _stateful_workload(iterations: int) -> int:
    value = yield Ask("seed")
    total = value
    for index in range(iterations):
        yield Log(f"iteration:{index}")
        yield Put("counter", index)
        total += index
    return total


def _run_once(interpreter: ProgramInterpreter, workload_iterations: int) -> None:
    ctx = ExecutionContext(env={"seed": 1})
    interpreter.run(_stateful_workload(workload_iterations), ctx)


def benchmark(runs: int, *, workload_iterations: int) -> dict[str, float]:
    interpreter = ProgramInterpreter()
    timings: list[float] = []

    for _ in range(runs):
        start = time.perf_counter()
        _run_once(interpreter, workload_iterations)
        elapsed = (time.perf_counter() - start) * 1000.0
        timings.append(elapsed)

    return {
        "runs": runs,
        "workload_iterations": workload_iterations,
        "min_ms": min(timings),
        "max_ms": max(timings),
        "mean_ms": statistics.mean(timings),
        "median_ms": statistics.median(timings),
    }


def format_report(results: Iterable[tuple[str, dict[str, float]]]) -> str:
    lines = ["doeff benchmark results:"]
    for label, stats in results:
        lines.append(f"  {label}:")
        lines.append(
            "    runs={runs} iterations={workload_iterations} | min={min_ms:.2f}ms "
            "median={median_ms:.2f}ms mean={mean_ms:.2f}ms max={max_ms:.2f}ms".format(**stats)
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark doeff interpreter execution")
    parser.add_argument("--runs", type=int, default=100, help="Number of interpreter executions")
    parser.add_argument(
        "--iterations",
        type=int,
        default=25,
        help="Inner loop iterations in the workload",
    )
    args = parser.parse_args()

    stats = benchmark(args.runs, workload_iterations=args.iterations)
    print(format_report([("stateful_workload", stats)]))


if __name__ == "main":  # pragma: no cover - CLI script
    main()
