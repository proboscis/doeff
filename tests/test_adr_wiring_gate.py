"""ADR-DOE-ENFORCE-001 R1: ADR wiring is part of the canonical pytest gate."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_all_executable_adrs_are_collected_by_default_pytest_gate() -> None:
    result: subprocess.CompletedProcess[str] = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "--doeff-adr-wiring=strict",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, (
        "doeff-adr wiring gate failed — executable ADR files must be collected by the "
        "canonical pytest gate (ADR-DOE-ENFORCE-001 R1).\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
