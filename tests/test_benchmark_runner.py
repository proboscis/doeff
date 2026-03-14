import csv
import json
from pathlib import Path

from benchmarks.benchmark_runner import format_report, run_benchmarks, write_report


def test_run_benchmarks_writes_json_and_csv_outputs(tmp_path: Path) -> None:
    report = run_benchmarks(runs=2, workload_iterations=3)
    output_paths = write_report(report, tmp_path)

    json_path = output_paths["json"]
    csv_path = output_paths["csv"]

    assert json_path.exists()
    assert csv_path.exists()

    payload = json.loads(json_path.read_text())
    assert payload["metadata"]["runs"] == 2
    assert payload["metadata"]["workload_iterations"] == 3
    assert {entry["name"] for entry in payload["results"]} == {
        "public_run:pure",
        "public_run:state",
        "public_run:state_writer",
    }

    with csv_path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 3
    assert {row["name"] for row in rows} == {
        "public_run:pure",
        "public_run:state",
        "public_run:state_writer",
    }


def test_format_report_mentions_generated_artifacts(tmp_path: Path) -> None:
    report = run_benchmarks(runs=1, workload_iterations=2)
    output_paths = write_report(report, tmp_path)

    summary = format_report(report, output_paths=output_paths)

    assert "doeff-vm benchmark results:" in summary
    assert "public_run:pure" in summary
    assert str(output_paths["json"]) in summary
    assert str(output_paths["csv"]) in summary
