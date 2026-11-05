"""An entry point that pins the pre-commit called by doeff's plain commit hook (scripts/git-hooks/pre-commit) to the version the repo's uv.lock decides.

The plain hook does not depend on the working tree's `.venv` (it runs in working trees where make sync was not done, and in the agora worker Pod's working tree). pre-commit is a dev
dependency, so as with semgrep, read the uv.lock version and call it from uv's tool store as `uv tool run --from pre-commit==<version> pre-commit`
(the version reading is in one place, scripts/semgrep_locked.py).

Usage: uv run --no-project --python <version> python scripts/precommit_locked.py <pre-commit arguments...> (at the repo root. Returns pre-commit's output and
exit value as they are). If uv.lock has no pre-commit, name the reason and stop with 1.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# The neighboring module (semgrep_locked) is read explicitly using this script's dir as the location (readable even under PYTHONSAFEPATH=1 -- the same as hook_finding_baseline).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from semgrep_locked import locked_command

PACKAGE: str = "pre-commit"


def main(argv: list[str]) -> int:
    """Replace the process, passing the arguments to the pre-commit of the version in the uv.lock at the repo root (the current dir)."""
    command: list[str] = locked_command(Path.cwd(), PACKAGE)
    os.execvp(command[0], [*command, *argv])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
