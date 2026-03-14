"""Comprehensive VM performance benchmarks for doeff.

Covers multiple workload patterns: pure computation, state effects,
reader effects, writer effects, nested handlers, and concurrent scheduling.

Usage
-----
    uv run python benchmarks/benchmark_vm.py
    uv run python benchmarks/benchmark_vm.py --runs 200 --csv results.csv
    uv run python benchmarks/benchmark_vm.py --scenario state --runs 500
"""

from __future__ import annotations

import argparse
import csv
import datetime
import statistics
import sys
import time
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

from doeff import (
    Ask,
    Gather,
    Get,
    Local,
    Modify,
    Put,
    Spawn,
    Tell,
    default_handlers,
    do,
    run,
)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchmarkResult:
    scenario: str
    runs: int
    parameter: int
    min_ms: float
    max_ms: float
    mean_ms: float
    median_ms: float
    stdev_ms: float
    p95_ms: float


# ---------------------------------------------------------------------------
# Workloads
# ---------------------------------------------------------------------------


@do
def pure_computation(depth: int) -> Generator[Any, Any, int]:
    """Pure yielded values with no effects — measures VM stepping overhead."""
    total: int = 0
    for i in range(depth):
        total += i * i
    return total


@do
def state_workload(iterations: int) -> Generator[Any, Any, int]:
    """State effects: Put/Get/Modify cycle."""
    yield Put("counter", 0)
    for i in range(iterations):
        yield Put("counter", i)
        val: int = yield Get("counter")
        yield Modify("counter", lambda x: x + 1)
    final: int = yield Get("counter")
    return final


@do
def reader_workload(iterations: int) -> Generator[Any, Any, int]:
    """Reader effects: Ask and Local."""
    total: int = 0
    for i in range(iterations):
        seed: int = yield Ask("seed")
        total += seed + i
    return total


@do
def writer_workload(iterations: int) -> Generator[Any, Any, int]:
    """Writer effects: Tell messages."""
    total: int = 0
    for i in range(iterations):
        yield Tell(f"step:{i}")
        total += i
    return total


@do
def mixed_effects_workload(iterations: int) -> Generator[Any, Any, int]:
    """Combined state + reader + writer effects."""
    seed: int = yield Ask("seed")
    yield Put("accumulator", seed)
    for i in range(iterations):
        current: int = yield Get("accumulator")
        yield Put("accumulator", current + i)
        yield Tell(f"iter:{i}")
    final: int = yield Get("accumulator")
    return final


@do
def nested_handler_workload(depth: int) -> Generator[Any, Any, int]:
    """Measures overhead of nested handler installation via Local scopes."""
    total: int = 0
    program = _nested_inner(depth, 0)
    result: int = yield program
    return result


@do
def _nested_inner(remaining: int, accumulator: int) -> Generator[Any, Any, int]:
    """Recursive Local nesting."""
    if remaining <= 0:
        return accumulator
    val: int = yield Ask("seed")
    inner_result: int = yield Local(
        {"seed": val + 1},
        _nested_inner(remaining - 1, accumulator + val),
    )
    return inner_result


@do
def spawn_gather_workload(num_tasks: int) -> Generator[Any, Any, int]:
    """Concurrent scheduling: Spawn tasks and Gather results."""
    tasks = []
    for i in range(num_tasks):
        task = yield Spawn(_spawn_child(i))
        tasks.append(task)
    results: tuple[int, ...] = yield Gather(*tasks)
    return sum(results)


@do
def _spawn_child(task_id: int) -> Generator[Any, Any, int]:
    """Simple child task for spawn benchmarks."""
    seed: int = yield Ask("seed")
    return seed + task_id


@do
def deep_call_chain(depth: int) -> Generator[Any, Any, int]:
    """Deeply nested @do program calls measuring call-stack overhead."""
    result: int = yield _chain_step(depth, 0)
    return result


@do
def _chain_step(remaining: int, accumulator: int) -> Generator[Any, Any, int]:
    """Single step in a deep call chain."""
    if remaining <= 0:
        return accumulator
    result: int = yield _chain_step(remaining - 1, accumulator + 1)
    return result


# ---------------------------------------------------------------------------
# Scenario registry
# ---------------------------------------------------------------------------

SCENARIOS: dict[str, dict[str, Any]] = {
    "pure": {
        "label": "pure_computation",
        "factory": pure_computation,
        "default_param": 100,
        "env": {},
        "store": {},
    },
    "state": {
        "label": "state_effects",
        "factory": state_workload,
        "default_param": 50,
        "env": {},
        "store": {},
    },
    "reader": {
        "label": "reader_effects",
        "factory": reader_workload,
        "default_param": 50,
        "env": {"seed": 1},
        "store": {},
    },
    "writer": {
        "label": "writer_effects",
        "factory": writer_workload,
        "default_param": 50,
        "env": {},
        "store": {},
    },
    "mixed": {
        "label": "mixed_effects",
        "factory": mixed_effects_workload,
        "default_param": 50,
        "env": {"seed": 1},
        "store": {},
    },
    "nested": {
        "label": "nested_handlers",
        "factory": nested_handler_workload,
        "default_param": 20,
        "env": {"seed": 1},
        "store": {},
    },
    "spawn": {
        "label": "spawn_gather",
        "factory": spawn_gather_workload,
        "default_param": 10,
        "env": {"seed": 1},
        "store": {},
    },
    "deep_call": {
        "label": "deep_call_chain",
        "factory": deep_call_chain,
        "default_param": 50,
        "env": {},
        "store": {},
    },
}


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------


def run_scenario(
    scenario_key: str,
    runs: int,
    param: int | None = None,
) -> BenchmarkResult:
    spec = SCENARIOS[scenario_key]
    effective_param: int = param if param is not None else spec["default_param"]
    factory = spec["factory"]
    env: dict[str, Any] = spec["env"]
    store: dict[str, Any] = spec["store"]

    # Warmup
    for _ in range(min(5, runs)):
        run(factory(effective_param), handlers=default_handlers(), env=env, store=store)

    timings: list[float] = []
    for _ in range(runs):
        start: float = time.perf_counter()
        run(factory(effective_param), handlers=default_handlers(), env=env, store=store)
        elapsed: float = (time.perf_counter() - start) * 1000.0
        timings.append(elapsed)

    sorted_timings: list[float] = sorted(timings)
    p95_index: int = max(0, int(len(sorted_timings) * 0.95) - 1)

    return BenchmarkResult(
        scenario=spec["label"],
        runs=runs,
        parameter=effective_param,
        min_ms=min(timings),
        max_ms=max(timings),
        mean_ms=statistics.mean(timings),
        median_ms=statistics.median(timings),
        stdev_ms=statistics.stdev(timings) if len(timings) > 1 else 0.0,
        p95_ms=sorted_timings[p95_index],
    )


def format_results(results: list[BenchmarkResult]) -> str:
    lines: list[str] = ["doeff-vm benchmark results", "=" * 60]
    for result in results:
        lines.append(f"  {result.scenario} (param={result.parameter}):")
        lines.append(
            f"    runs={result.runs}"
            f"  min={result.min_ms:.3f}ms"
            f"  median={result.median_ms:.3f}ms"
            f"  mean={result.mean_ms:.3f}ms"
            f"  p95={result.p95_ms:.3f}ms"
            f"  max={result.max_ms:.3f}ms"
            f"  stdev={result.stdev_ms:.3f}ms"
        )
    return "\n".join(lines)


def write_csv(results: list[BenchmarkResult], csv_path: str) -> None:
    fieldnames: list[str] = [
        "timestamp",
        "scenario",
        "parameter",
        "runs",
        "min_ms",
        "median_ms",
        "mean_ms",
        "p95_ms",
        "max_ms",
        "stdev_ms",
    ]
    timestamp: str = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()

    with open(csv_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if f.tell() == 0:
            writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "timestamp": timestamp,
                    "scenario": result.scenario,
                    "parameter": result.parameter,
                    "runs": result.runs,
                    "min_ms": f"{result.min_ms:.4f}",
                    "median_ms": f"{result.median_ms:.4f}",
                    "mean_ms": f"{result.mean_ms:.4f}",
                    "p95_ms": f"{result.p95_ms:.4f}",
                    "max_ms": f"{result.max_ms:.4f}",
                    "stdev_ms": f"{result.stdev_ms:.4f}",
                }
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="doeff-vm performance benchmarks",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=100,
        help="Number of timed iterations per scenario (default: 100)",
    )
    parser.add_argument(
        "--param",
        type=int,
        default=None,
        help="Override the workload parameter for all scenarios",
    )
    parser.add_argument(
        "--scenario",
        type=str,
        default=None,
        choices=list(SCENARIOS.keys()),
        help="Run a single scenario instead of all",
    )
    parser.add_argument(
        "--csv",
        type=str,
        default=None,
        help="Append results to CSV file for tracking over time",
    )
    return parser


def main() -> None:
    parser: argparse.ArgumentParser = build_parser()
    args: argparse.Namespace = parser.parse_args()

    scenario_keys: list[str] = (
        [args.scenario] if args.scenario else list(SCENARIOS.keys())
    )

    results: list[BenchmarkResult] = []
    for key in scenario_keys:
        result: BenchmarkResult = run_scenario(key, args.runs, args.param)
        results.append(result)

    report: str = format_results(results)
    sys.stdout.write(report + "\n")

    if args.csv:
        write_csv(results, args.csv)
        sys.stdout.write(f"\nResults appended to {args.csv}\n")


if __name__ == "__main__":
    main()
