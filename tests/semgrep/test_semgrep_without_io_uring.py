"""semgrep inside the checks still answers on a machine whose io_uring budget is exhausted (the root conftest.py sets EIO_BACKEND=posix).

On Linux, semgrep-core (OCaml 5 + Eio) opens an io_uring ring per domain, and the kernel counts ring memory against the per-uid
RLIMIT_MEMLOCK -- one budget shared by all processes of the same uid (8 MiB in an agent Pod). When semgrep runs of the same uid on the same machine
overlap, the next ring cannot be created because `io_uring_queue_init` fails with ENOMEM, and semgrep dies with "semgrep-core rule validation failed" with no findings
(in the 2026-10-16 full run at the root, tests/semgrep/test_public_api_subpackage_imports_rule.py was red and green on rerun).
The exhausted state is reproduced by lowering the child's RLIMIT_MEMLOCK to 16 KiB (it is exhausted every time regardless of what the machine has left).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.semgrep

RULE = """\
rules:
  - id: wall-clock-sleep
    languages: [python]
    severity: ERROR
    message: sleeps on the wall clock
    pattern: time.sleep(...)
"""
TARGET = "import time\n\ntime.sleep(1)\n"
# Lower the locked-memory limit in a child sh, then exec semgrep (ulimit is a sh builtin -- lowering it needs no privilege)
SPENT_BUDGET = 'ulimit -l 16 && exec "$@"'


def test_semgrep_answers_when_the_locked_memory_budget_is_spent(
    tmp_path: Path, per_test_deadline: float | None
) -> None:
    semgrep = shutil.which("semgrep")
    assert semgrep is not None, "semgrep is a dev dependency -- run `make sync`"
    rule = tmp_path / "rule.yaml"
    rule.write_text(RULE, encoding="utf-8")
    target = tmp_path / "target.py"
    target.write_text(TARGET, encoding="utf-8")
    completed = subprocess.run(
        [
            "sh",
            "-c",
            SPENT_BUDGET,
            "sh",
            semgrep,
            "--metrics=off",
            "--disable-version-check",
            "--config",
            str(rule),
            "--json",
            "--quiet",
            str(target),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=per_test_deadline,
        check=False,
    )
    # With a backend that uses io_uring, it exits 2 here and stdout is not JSON ("semgrep-core rule validation failed")
    assert completed.returncode in {0, 1}, (
        f"semgrep died because the io_uring budget was exhausted (exit {completed.returncode}):\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    payload = json.loads(completed.stdout)
    assert payload["errors"] == []
    assert [finding["start"]["line"] for finding in payload["results"]] == [3]
