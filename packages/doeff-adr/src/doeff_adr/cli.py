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
    semgrep_hy = subparsers.add_parser(
        "semgrep-hy",
        help="Run Semgrep python rules over .hy/.py files, reading Hy through macro expansion.",
    )
    semgrep_hy.add_argument("--config", required=True, help="Semgrep config file.")
    semgrep_hy.add_argument(
        "--root", default=".", help="Project root (paths and imports are relative to it)."
    )
    semgrep_hy.add_argument(
        "--python-path",
        action="append",
        default=[],
        help="Extra import path while expanding (repeatable).",
    )
    semgrep_hy.add_argument("targets", nargs="+", help="Files or directories under --root.")
    return parser


def _semgrep_hy(arguments: argparse.Namespace) -> int:
    from pathlib import Path

    from doeff_adr.semgrep_hy import scan_with_hy_expansion

    findings = scan_with_hy_expansion(
        Path(arguments.config),
        Path(arguments.root),
        arguments.targets,
        python_path=tuple(arguments.python_path),
    )
    for finding in findings:
        print(f"{finding.path}:{finding.line}: {finding.short_rule_id}: {finding.message}")
    print(f"{len(findings)} finding(s)")
    return 1 if findings else 0


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
    if arguments.command == "semgrep-hy":
        return _semgrep_hy(arguments)
    raise AssertionError(f"unhandled doeff-adr command: {arguments.command}")
