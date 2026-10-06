"""CLI wrapper that executes the bundled doeff-indexer binary.

This module provides a Python entry point that locates and executes
the bundled native binary, avoiding Python interpreter startup overhead
for CLI invocations.
"""


import os
import subprocess
import sys
from pathlib import Path


def _get_binary_name() -> str:
    """Get the platform-specific binary name."""
    if sys.platform == "win32":
        return "doeff-indexer.exe"
    return "doeff-indexer"


def _get_binary_path() -> Path | None:
    """Locate the bundled binary.

    Returns:
        Path to the binary if found, None otherwise.
    """
    # 探し先は package の __path__ の順(editable の入れでは venv の組み立ての成果物の dir → 作業木の dir — doeff の build の口の
    # editable の finder・agora-redesign #3860。普通の入れでは package の dir 1 つ)。
    package = sys.modules[__package__ or "doeff_indexer"]
    found = (
        Path(place) / "bin" / _get_binary_name() for place in package.__path__
    )
    return next((binary for binary in found if binary.exists() and os.access(binary, os.X_OK)), None)


def main() -> int:
    """Execute the bundled doeff-indexer binary.

    Returns:
        Exit code from the binary execution.
    """
    binary = _get_binary_path()

    if binary is None:
        # Binary not found - provide helpful error message
        print(
            "Error: doeff-indexer binary not found.\n"
            "This may indicate:\n"
            "  - A corrupted installation\n"
            "  - Missing platform-specific binary in the wheel\n"
            "\n"
            "Try reinstalling: pip install --force-reinstall doeff-indexer",
            file=sys.stderr,
        )
        return 1

    # Execute the binary with all arguments passed through
    try:
        return subprocess.call([str(binary)] + sys.argv[1:])
    except OSError as e:
        print(f"Error executing doeff-indexer: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

