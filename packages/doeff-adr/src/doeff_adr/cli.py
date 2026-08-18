"""Command-line entry point for doeff-adr repository checks."""

import argparse
import sys
from collections.abc import Sequence

from doeff_adr.process import ExternalProcessTimeoutError, run_external_process

# GNU timeout と同じ「時間切れ」の終了コード — pytest 自身の終了コード(0..5)と
# 衝突しないため、門の呼び出し側が collection の赤と打ち切りを取り違えない。
TIMEOUT_EXIT_CODE = 124


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
        # 打ち切りは「配線検証 OK」ではない — 非ゼロで落として診断を stderr に残す。
        try:
            completed = run_external_process(command)
        except ExternalProcessTimeoutError as exc:
            sys.stderr.write(
                f"doeff-adr verify-wiring: pytest --collect-only was killed after "
                f"{exc.timeout_seconds:g}s without returning — wiring is UNVERIFIED "
                f"(not verified).\n{exc}\n"
            )
            return TIMEOUT_EXIT_CODE
        if completed.returncode == 0:
            print("doeff-adr wiring verified: every executable ADR was collected.")
            return 0
        sys.stdout.write(completed.stdout)
        sys.stderr.write(completed.stderr)
        return completed.returncode
    raise AssertionError(f"unhandled doeff-adr command: {arguments.command}")
