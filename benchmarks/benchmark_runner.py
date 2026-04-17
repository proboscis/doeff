"""Benchmark runner for the public doeff Python API backed by doeff-vm.

Usage
-----
    uv run python benchmarks/benchmark_runner.py --runs 500 --iterations 25
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from benchmarks.pyvm_workloads import build_public_benchmark_cases


@dataclass(frozen=True)
class BenchmarkMeasurement:
    name: str
    runner: str
    workload: str
    runs: int
    workload_iterations: int
    expected_value: int
    min_ms: float
    max_ms: float
    mean_ms: float
    median_ms: float


@dataclass(frozen=True)
class BenchmarkReport:
    generated_at: str
    runs: int
    workload_iterations: int
    results: list[BenchmarkMeasurement]


def _measure_case(case, *, runs: int, workload_iterations: int) -> BenchmarkMeasurement:
    observed = case.invoke()
    if observed != case.expected_value:
        raise AssertionError(
            f"{case.name} returned {observed!r}, expected {case.expected_value!r}"
        )

    timings: list[float] = []
    last_value = observed
    for _ in range(runs):
        start = time.perf_counter()
        last_value = case.invoke()
        elapsed = (time.perf_counter() - start) * 1000.0
        timings.append(elapsed)

    if last_value != case.expected_value:
        raise AssertionError(
            f"{case.name} returned {last_value!r} after timing, expected {case.expected_value!r}"
        )

    return BenchmarkMeasurement(
        name=case.name,
        runner=case.runner,
        workload=case.workload,
        runs=runs,
        workload_iterations=workload_iterations,
        expected_value=case.expected_value,
        min_ms=min(timings),
        max_ms=max(timings),
        mean_ms=statistics.mean(timings),
        median_ms=statistics.median(timings),
    )


def run_benchmarks(*, runs: int, workload_iterations: int) -> BenchmarkReport:
    cases = build_public_benchmark_cases(workload_iterations)
    results = [
        _measure_case(case, runs=runs, workload_iterations=workload_iterations) for case in cases
    ]
    return BenchmarkReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        runs=runs,
        workload_iterations=workload_iterations,
        results=results,
    )


def write_report(report: BenchmarkReport, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "doeff_vm_benchmark_results.json"
    csv_path = output_dir / "doeff_vm_benchmark_results.csv"

    json_payload = {
        "metadata": {
            "generated_at": report.generated_at,
            "runs": report.runs,
            "workload_iterations": report.workload_iterations,
        },
        "results": [asdict(result) for result in report.results],
    }
    json_path.write_text(json.dumps(json_payload, indent=2, sort_keys=True) + "\n")

    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "name",
                "runner",
                "workload",
                "runs",
                "workload_iterations",
                "expected_value",
                "min_ms",
                "max_ms",
                "mean_ms",
                "median_ms",
            ],
        )
        writer.writeheader()
        for result in report.results:
            writer.writerow(asdict(result))

    return {"json": json_path, "csv": csv_path}


def format_report(report: BenchmarkReport, *, output_paths: dict[str, Path] | None = None) -> str:
    lines = ["doeff-vm benchmark results:"]
    for result in report.results:
        lines.append(
            "  {name}: runs={runs} iterations={workload_iterations} | "
            "min={min_ms:.2f}ms median={median_ms:.2f}ms "
            "mean={mean_ms:.2f}ms max={max_ms:.2f}ms".format(**asdict(result))
        )
    if output_paths is not None:
        lines.append(f"  json={output_paths['json']}")
        lines.append(f"  csv={output_paths['csv']}")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark doeff-vm through the public Python API")
    parser.add_argument("--runs", type=int, default=100, help="Number of executions per workload")
    parser.add_argument(
        "--iterations",
        type=int,
        default=25,
        help="Inner loop iterations for stateful workloads",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("benchmarks/results"),
        help="Directory for JSON and CSV benchmark artifacts",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = run_benchmarks(runs=args.runs, workload_iterations=args.iterations)
    output_paths = write_report(report, args.output_dir)
    print(format_report(report, output_paths=output_paths))


if __name__ == "__main__":  # pragma: no cover - CLI script
    main()
