"""Profiling utilities for CLI operations.

Profiling is enabled by default. To disable:
    export DOEFF_DISABLE_PROFILE=1
"""

import os
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager

PROFILING_ENABLED = not bool(os.environ.get("DOEFF_DISABLE_PROFILE"))


@contextmanager
def profile(operation: str, *, indent: int = 0) -> Generator[None, None, None]:
    if not PROFILING_ENABLED:
        yield
        return

    indent_str = "  " * indent
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        print(f"[DOEFF][PROFILE] {indent_str}{operation}: {elapsed_ms:.2f}ms", file=sys.stderr)


def is_profiling_enabled() -> bool:
    return PROFILING_ENABLED


def print_profiling_status() -> None:
    if PROFILING_ENABLED:
        print(
            "[DOEFF][PROFILE] Profiling enabled. To disable: export DOEFF_DISABLE_PROFILE=1",
            file=sys.stderr,
        )
