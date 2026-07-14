"""Command-line entry point for doeff-adr repository checks."""

import argparse
import subprocess
import sys
from collections.abc import Sequence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="doeff-adr")
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify_wiring = subparsers.add_parser(
        "verify-wiring",
        help="Fail when an executable ADR is outside the effective pytest collection scope.",
    )
    verify_wiring.add_argument(
        "pytest_args",
        nargs=argparse.REMAINDER,
        help="Optional pytest paths or collection arguments.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "verify-wiring":
        command: list[str] = [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            *arguments.pytest_args,
            "--doeff-adr-wiring=strict",
        ]
        completed: subprocess.CompletedProcess[str] = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode == 0:
            print("doeff-adr wiring verified: every executable ADR was collected.")
            return 0
        sys.stdout.write(completed.stdout)
        sys.stderr.write(completed.stderr)
        return completed.returncode
    raise AssertionError(f"unhandled doeff-adr command: {arguments.command}")
