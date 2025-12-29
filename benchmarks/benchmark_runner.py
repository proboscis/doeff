"""Micro-benchmarks for the doeff interpreter.

Usage
-----
    uv run python benchmarks/benchmark_runner.py --runs 500

Compares ProgramInterpreter (original) vs TrampolinedInterpreter (new).
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from typing import Any, Iterable, Protocol

from doeff import ExecutionContext, ProgramInterpreter, do
from doeff.effects import Ask, Log, Put
from doeff.interpreter_v2 import TrampolinedInterpreter


class InterpreterProtocol(Protocol):
    """Protocol for interpreter implementations."""
    def run(self, program: Any, context: ExecutionContext | None = None) -> Any: ...


@do
def _stateful_workload(iterations: int) -> int:
    value = yield Ask("seed")
    total = value
    for index in range(iterations):
        yield Log(f"iteration:{index}")
        yield Put("counter", index)
        total += index
    return total


def _run_once(interpreter: InterpreterProtocol, workload_iterations: int) -> None:
    ctx = ExecutionContext(env={"seed": 1})
    interpreter.run(_stateful_workload(workload_iterations), ctx)


def benchmark(
    interpreter: InterpreterProtocol,
    runs: int,
    *,
    workload_iterations: int,
    label: str = "interpreter"
) -> dict[str, float]:
    """Run benchmark for a specific interpreter implementation."""
    timings: list[float] = []

    for _ in range(runs):
        start = time.perf_counter()
        _run_once(interpreter, workload_iterations)
        elapsed = (time.perf_counter() - start) * 1000.0
        timings.append(elapsed)

    return {
        "label": label,
        "runs": runs,
        "workload_iterations": workload_iterations,
        "min_ms": min(timings),
        "max_ms": max(timings),
        "mean_ms": statistics.mean(timings),
        "median_ms": statistics.median(timings),
    }


def benchmark_comparison(
    runs: int,
    *,
    workload_iterations: int
) -> tuple[dict[str, float], dict[str, float], float]:
    """Compare ProgramInterpreter vs TrampolinedInterpreter.

    Returns:
        Tuple of (old_stats, new_stats, regression_percent)
    """
    old_interpreter = ProgramInterpreter()
    new_interpreter = TrampolinedInterpreter()

    old_stats = benchmark(
        old_interpreter, runs,
        workload_iterations=workload_iterations,
        label="ProgramInterpreter"
    )
    new_stats = benchmark(
        new_interpreter, runs,
        workload_iterations=workload_iterations,
        label="TrampolinedInterpreter"
    )

    # Calculate regression percentage (positive = slower, negative = faster)
    regression = ((new_stats["median_ms"] - old_stats["median_ms"]) / old_stats["median_ms"]) * 100

    return old_stats, new_stats, regression


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
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Compare ProgramInterpreter vs TrampolinedInterpreter",
    )
    parser.add_argument(
        "--regression-threshold",
        type=float,
        default=10.0,
        help="Maximum allowed regression percentage (default: 10%%)",
    )
    args = parser.parse_args()

    if args.compare:
        old_stats, new_stats, regression = benchmark_comparison(
            args.runs, workload_iterations=args.iterations
        )
        print(format_report([
            ("ProgramInterpreter (baseline)", old_stats),
            ("TrampolinedInterpreter (new)", new_stats),
        ]))
        print()
        if regression > 0:
            print(f"  Regression: +{regression:.2f}% (new is slower)")
        else:
            print(f"  Improvement: {-regression:.2f}% (new is faster)")

        if regression > args.regression_threshold:
            print(f"\n  WARNING: Regression exceeds threshold of {args.regression_threshold}%!")
            exit(1)
        else:
            print(f"\n  OK: Within acceptable threshold of {args.regression_threshold}%")
    else:
        # Default: just benchmark ProgramInterpreter (backward compatible)
        interpreter = ProgramInterpreter()
        stats = benchmark(
            interpreter, args.runs,
            workload_iterations=args.iterations,
            label="stateful_workload"
        )
        print(format_report([("stateful_workload", stats)]))


if __name__ == "__main__":  # pragma: no cover - CLI script
    main()
