#!/usr/bin/env python3
"""Run a simple smoke command across multiple Python interpreters via `uv run`."""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from collections import OrderedDict
from typing import Iterable, Sequence

DEFAULT_VERSIONS: tuple[str, ...] = ("3.10", "3.11", "3.12", "3.13", "3.14t")
DEFAULT_COMMAND: Sequence[str] = (
    "python",
    "-c",
    "import doeff, sys; sys.stdout.write(doeff.__version__ + '\\n')",
)


def parse_versions(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def parse_command(raw: str | None, remainder: Sequence[str]) -> list[str]:
    if remainder:
        return list(remainder)
    if raw:
        return shlex.split(raw)
    return list(DEFAULT_COMMAND)


def build_uv_command(
    version: str,
    command: Sequence[str],
    extras: Iterable[str],
    no_sync: bool,
) -> list[str]:
    uv_cmd: list[str] = ["uv", "run", "--python", version]
    for extra in extras:
        uv_cmd.extend(["--extra", extra])
    if no_sync:
        uv_cmd.append("--no-sync")
    uv_cmd.extend(command)
    return uv_cmd


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Execute a command with uv across a Python version matrix "
            "(defaults to importing doeff)."
        )
    )
    parser.add_argument(
        "--versions",
        default=",".join(DEFAULT_VERSIONS),
        help="Comma separated Python versions to test (default: %(default)s).",
    )
    parser.add_argument(
        "--cmd",
        help=(
            "Command string to execute (shlex parsed). "
            "If provided, overrides the default smoke import."
        ),
    )
    parser.add_argument(
        "--extra",
        action="append",
        default=[],
        help="Pass `--extra <name>` to uv for the given dependency extra.",
    )
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Forward `--no-sync` to uv (useful when environments are already prepared).",
    )
    parser.add_argument(
        "remainder",
        nargs=argparse.REMAINDER,
        help="Command to execute, supplied after `--`.",
    )

    args = parser.parse_args(argv)

    versions = parse_versions(args.versions)
    if not versions:
        parser.error("No Python versions specified.")

    command = parse_command(args.cmd, args.remainder)
    results: dict[str, int] = OrderedDict()

    for version in versions:
        uv_cmd = build_uv_command(version, command, args.extra, args.no_sync)
        print(f"\n=== Python {version} ===")
        print("$", " ".join(shlex.quote(token) for token in uv_cmd))
        completed = subprocess.run(uv_cmd, check=False)
        results[version] = completed.returncode
        if completed.returncode != 0:
            print(f"[!] Command failed for Python {version} (exit {completed.returncode})")

    print("\nSummary:")
    for version, returncode in results.items():
        status = "ok" if returncode == 0 else f"failed ({returncode})"
        print(f"- {version}: {status}")

    failures = [version for version, code in results.items() if code != 0]
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
