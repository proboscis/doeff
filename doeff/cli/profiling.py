"""Profiling utilities for CLI operations.  # noqa: PINJ036

Enable profiling by setting the DOEFF_PROFILE environment variable:
    export DOEFF_PROFILE=1
    doeff run --program myapp.main
"""

import os
import sys
import time
from contextlib import contextmanager
from typing import Generator


# Profiling is a debug feature that legitimately needs env var check
PROFILING_ENABLED = bool(os.environ.get("DOEFF_PROFILE"))  # noqa: PINJ050


@contextmanager
def profile(operation: str, *, indent: int = 0) -> Generator[None, None, None]:
    """Profile an operation if DOEFF_PROFILE is enabled.

    Args:
        operation: Description of the operation being profiled
        indent: Indentation level for hierarchical display (0 = root, 1 = child, etc.)

    Example:
        with profile("Import modules"):
            import heavy_module

        with profile("Nested operation", indent=1):
            do_something()
    """
    if not PROFILING_ENABLED:
        yield
        return

    indent_str = "  " * indent
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        # Write to stderr to avoid interfering with stdout (JSON output, etc.)
        print(f"[PROFILE] {indent_str}{operation}: {elapsed_ms:.2f}ms", file=sys.stderr)


def is_profiling_enabled() -> bool:
    """Check if profiling is enabled."""
    return PROFILING_ENABLED
