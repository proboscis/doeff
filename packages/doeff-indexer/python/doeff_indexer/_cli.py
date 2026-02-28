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
    package_dir = Path(__file__).parent
    binary = package_dir / "bin" / _get_binary_name()

    if binary.exists() and os.access(binary, os.X_OK):
        return binary

    return None


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

