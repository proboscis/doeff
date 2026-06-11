"""End-to-end benchmarks for the doeff Python runtime.

Usage
-----
    uv run python benchmarks/benchmark_runner.py --runs 20
    uv run python benchmarks/benchmark_runner.py --smoke --no-output
    uv run python benchmarks/benchmark_runner.py --compare benchmarks/results/baseline.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import socket
import statistics
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from doeff_core_effects import (
    Ask,
    Await,
    Gather,
    Get,
    Put,
    Spawn,
    await_handler,
    reader,
    scheduled,
    state,
)
from doeff_vm import Callable as VmCallable
from doeff_vm import EffectBase

from doeff import Apply, Pass, Pure, Resume, do, run
from doeff.program import WithHandlerType as VMWithHandler

DEFAULT_RUNS = 20
DEFAULT_LOOP_ITERATIONS = 100
DEFAULT_AWAIT_ITERATIONS = 100
DEFAULT_BOUNDARY_ITERATIONS = 1_000
DEFAULT_SPAWN_SIZES = (100, 1_000)
RESULTS_DIR = Path("benchmarks/results")


@dataclass(frozen=True)
class BenchmarkConfig:
    runs: int
    loop_iterations: int
    await_iterations: int
    boundary_iterations: int
    spawn_sizes: tuple[int, ...]
    smoke: bool = False

    @classmethod
    def smoke_config(cls) -> BenchmarkConfig:
        return cls(
            runs=1,
            loop_iterations=1,
            await_iterations=1,
            boundary_iterations=1,
            spawn_sizes=(1,),
            smoke=True,
        )


@dataclass(frozen=True)
class BenchmarkStats:
    name: str
    runs: int
    unit: str
    units_per_run: int
    parameters: dict[str, int | str | bool]
    min_ms: float
    median_ms: float
    mean_ms: float
    max_ms: float
    mean_unit_us: float
    throughput_per_s: float


class PythonCallableEffect(EffectBase):
    """Effect carrying a Python callable wrapped for the Rust VM."""

    def __init__(self, callback: VmCallable, value: int) -> None:
        super().__init__()
        self.callback = callback
        self.value = value


@do
def _state_get_put_loop(iterations: int) -> Any:
    for _index in range(iterations):
        counter = yield Get("counter")
        yield Put("counter", counter + 1)
    return (yield Get("counter"))


@do
def _reader_ask_loop(iterations: int) -> Any:
    total = 0
    for _index in range(iterations):
        value = yield Ask("value")
        total += value
    return total


@do
def _spawn_gather_program(task_count: int) -> Any:
    tasks = []
    for _index in range(task_count):
        task = yield Spawn(_noop_task())
        tasks.append(task)
    return (yield Gather(*tasks))


@do
def _noop_task() -> None:
    return None


@do
def _await_loop(iterations: int) -> Any:
    import asyncio

    for _index in range(iterations):
        yield Await(asyncio.sleep(0))
    return iterations


@do
def _python_callable_effect_handler(effect: Any, k: Any) -> Any:
    if isinstance(effect, PythonCallableEffect):
        result = yield Apply(Pure(effect.callback), [Pure(effect.value)])
        return (yield Resume(k, result))
    yield Pass(effect, k)


@do
def _python_callable_effect_loop(iterations: int, callback: VmCallable) -> Any:
    total = 0
    for index in range(iterations):
        total += yield PythonCallableEffect(callback, index)
    return total


def _increment(value: int) -> int:
    return value + 1


def _measure(
    name: str,
    *,
    runs: int,
    unit: str,
    units_per_run: int,
    parameters: dict[str, int | str | bool],
    workload: Callable[[], Any],
    validate: Callable[[Any], None],
) -> BenchmarkStats:
    timings: list[float] = []

    for _index in range(runs):
        start = time.perf_counter()
        result = workload()
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        validate(result)
        timings.append(elapsed_ms)

    mean_ms = statistics.mean(timings)
    mean_unit_us = (mean_ms * 1_000.0) / max(units_per_run, 1)
    throughput_per_s = (units_per_run * 1_000.0) / mean_ms if mean_ms > 0.0 else 0.0

    return BenchmarkStats(
        name=name,
        runs=runs,
        unit=unit,
        units_per_run=units_per_run,
        parameters=parameters,
        min_ms=min(timings),
        median_ms=statistics.median(timings),
        mean_ms=mean_ms,
        max_ms=max(timings),
        mean_unit_us=mean_unit_us,
        throughput_per_s=throughput_per_s,
    )


def _run_trivial() -> Any:
    return run(Pure(1))


def _run_state_get_put_loop(iterations: int) -> Any:
    return run(state({"counter": 0})(_state_get_put_loop(iterations)))


def _run_reader_ask_loop(iterations: int) -> Any:
    return run(reader({"value": 1})(_reader_ask_loop(iterations)))


def _run_spawn_gather(task_count: int) -> Any:
    return run(scheduled(_spawn_gather_program(task_count)))


def _run_await_round_trip(iterations: int, handler: Callable[..., Any]) -> Any:
    return run(scheduled(handler(_await_loop(iterations))))


def _run_python_callable_boundary(iterations: int, callback: VmCallable) -> Any:
    program = VMWithHandler(
        _python_callable_effect_handler,
        _python_callable_effect_loop(iterations, callback),
    )
    return run(program)


def run_benchmarks(config: BenchmarkConfig) -> list[BenchmarkStats]:
    await_effect_handler = await_handler()
    boundary_callback = VmCallable(_increment)
    results: list[BenchmarkStats] = []

    results.append(
        _measure(
            "run_trivial",
            runs=config.runs,
            unit="run",
            units_per_run=1,
            parameters={"program": "Pure(1)"},
            workload=_run_trivial,
            validate=lambda result: _assert_equal(result, 1),
        )
    )
    results.append(
        _measure(
            "run_state_get_put_loop",
            runs=config.runs,
            unit="iteration",
            units_per_run=config.loop_iterations,
            parameters={"iterations": config.loop_iterations},
            workload=lambda: _run_state_get_put_loop(config.loop_iterations),
            validate=lambda result: _assert_equal(result, config.loop_iterations),
        )
    )
    results.append(
        _measure(
            "run_reader_ask_loop",
            runs=config.runs,
            unit="iteration",
            units_per_run=config.loop_iterations,
            parameters={"iterations": config.loop_iterations},
            workload=lambda: _run_reader_ask_loop(config.loop_iterations),
            validate=lambda result: _assert_equal(result, config.loop_iterations),
        )
    )

    for task_count in config.spawn_sizes:
        results.append(
            _measure(
                f"spawn_gather_{task_count}",
                runs=config.runs,
                unit="task",
                units_per_run=task_count,
                parameters={"tasks": task_count},
                workload=lambda task_count=task_count: _run_spawn_gather(task_count),
                validate=lambda result, task_count=task_count: _assert_equal(
                    len(result),
                    task_count,
                ),
            )
        )

    results.append(
        _measure(
            "await_sleep_0_round_trip",
            runs=config.runs,
            unit="await",
            units_per_run=config.await_iterations,
            parameters={"iterations": config.await_iterations},
            workload=lambda: _run_await_round_trip(config.await_iterations, await_effect_handler),
            validate=lambda result: _assert_equal(result, config.await_iterations),
        )
    )
    results.append(
        _measure(
            "python_callable_boundary",
            runs=config.runs,
            unit="call",
            units_per_run=config.boundary_iterations,
            parameters={"iterations": config.boundary_iterations},
            workload=lambda: _run_python_callable_boundary(
                config.boundary_iterations,
                boundary_callback,
            ),
            validate=lambda result: _assert_equal(
                result,
                config.boundary_iterations * (config.boundary_iterations + 1) // 2,
            ),
        )
    )

    return results


def _assert_equal(actual: Any, expected: Any) -> None:
    if actual != expected:
        raise AssertionError(f"expected {expected!r}, got {actual!r}")


def build_payload(config: BenchmarkConfig, results: Sequence[BenchmarkStats]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "config": asdict(config),
        "results": [asdict(result) for result in results],
    }


def default_results_path() -> Path:
    date_label = dt.datetime.now(dt.UTC).strftime("%Y%m%d")
    host_label = socket.gethostname().split(".", maxsplit=1)[0]
    return RESULTS_DIR / f"{date_label}-{host_label}.json"


def write_results(payload: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def load_results(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def benchmark(runs: int, *, workload_iterations: int) -> dict[str, float]:
    """Compatibility wrapper for the original stateful benchmark shape."""

    result = _measure(
        "run_state_get_put_loop",
        runs=runs,
        unit="iteration",
        units_per_run=workload_iterations,
        parameters={"iterations": workload_iterations},
        workload=lambda: _run_state_get_put_loop(workload_iterations),
        validate=lambda actual: _assert_equal(actual, workload_iterations),
    )
    return {
        "runs": float(result.runs),
        "workload_iterations": float(workload_iterations),
        "min_ms": result.min_ms,
        "max_ms": result.max_ms,
        "mean_ms": result.mean_ms,
        "median_ms": result.median_ms,
    }


def format_report(results: Iterable[BenchmarkStats | tuple[str, dict[str, float]]]) -> str:
    lines = ["doeff benchmark results:"]
    for item in results:
        if isinstance(item, BenchmarkStats):
            lines.append(
                f"  {item.name}: runs={item.runs} units/run={item.units_per_run} {item.unit} | "
                f"min={item.min_ms:.2f}ms median={item.median_ms:.2f}ms "
                f"mean={item.mean_ms:.2f}ms max={item.max_ms:.2f}ms "
                f"mean_unit={item.mean_unit_us:.2f}us "
                f"throughput={item.throughput_per_s:.1f}/s"
            )
        else:
            label, stats = item
            lines.append(f"  {label}:")
            lines.append(
                "    runs={runs:.0f} iterations={workload_iterations:.0f} | "
                "min={min_ms:.2f}ms median={median_ms:.2f}ms "
                "mean={mean_ms:.2f}ms max={max_ms:.2f}ms".format(**stats)
            )
    return "\n".join(lines)


def format_comparison(current: Sequence[BenchmarkStats], baseline_payload: dict[str, Any]) -> str:
    baseline_results = {
        result["name"]: result
        for result in baseline_payload.get("results", [])
        if isinstance(result, dict) and "name" in result
    }
    lines = ["comparison against baseline:"]

    for result in current:
        baseline = baseline_results.get(result.name)
        if baseline is None:
            lines.append(f"  {result.name}: no baseline")
            continue
        baseline_mean = float(baseline["mean_ms"])
        delta_pct = (
            ((result.mean_ms - baseline_mean) / baseline_mean) * 100.0
            if baseline_mean > 0.0
            else 0.0
        )
        lines.append(
            f"  {result.name}: current={result.mean_ms:.2f}ms "
            f"baseline={baseline_mean:.2f}ms delta={delta_pct:+.1f}%"
        )

    return "\n".join(lines)


def _parse_spawn_sizes(raw_value: str) -> tuple[int, ...]:
    values = tuple(int(part.strip()) for part in raw_value.split(",") if part.strip())
    if not values:
        raise argparse.ArgumentTypeError("at least one spawn size is required")
    return values


def _config_from_args(args: argparse.Namespace) -> BenchmarkConfig:
    if args.smoke:
        return BenchmarkConfig.smoke_config()
    return BenchmarkConfig(
        runs=args.runs,
        loop_iterations=args.iterations,
        await_iterations=args.await_iterations,
        boundary_iterations=args.boundary_iterations,
        spawn_sizes=args.spawn_sizes,
        smoke=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark doeff runtime execution")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="Number of runs per case")
    parser.add_argument(
        "--iterations",
        type=int,
        default=DEFAULT_LOOP_ITERATIONS,
        help="Inner loop iterations for run(), state, and reader cases",
    )
    parser.add_argument(
        "--await-iterations",
        type=int,
        default=DEFAULT_AWAIT_ITERATIONS,
        help="Sequential Await(asyncio.sleep(0)) operations per run",
    )
    parser.add_argument(
        "--boundary-iterations",
        type=int,
        default=DEFAULT_BOUNDARY_ITERATIONS,
        help="Python callable boundary operations per run",
    )
    parser.add_argument(
        "--spawn-sizes",
        type=_parse_spawn_sizes,
        default=DEFAULT_SPAWN_SIZES,
        help="Comma-separated Spawn+Gather task counts",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Run one iteration with the smallest N values for CI smoke checks",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="JSON output path; defaults to benchmarks/results/<yyyymmdd>-<hostname>.json",
    )
    parser.add_argument(
        "--no-output",
        action="store_true",
        help="Do not write a JSON results file",
    )
    parser.add_argument(
        "--compare",
        type=Path,
        default=None,
        help="Print a mean-time comparison against a baseline JSON file",
    )
    args = parser.parse_args()

    config = _config_from_args(args)
    results = run_benchmarks(config)
    payload = build_payload(config, results)

    print(format_report(results))

    if args.compare is not None:
        print(format_comparison(results, load_results(args.compare)))

    if not args.no_output:
        output_path = args.output or default_results_path()
        write_results(payload, output_path)
        print(f"wrote JSON results: {output_path}")


if __name__ == "__main__":  # pragma: no cover - CLI script
    main()
