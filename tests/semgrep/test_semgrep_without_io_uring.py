"""検の中の semgrep は、io_uring の予算が尽きた機体でも答える(根の conftest.py が EIO_BACKEND=posix を置く)。

semgrep-core(OCaml 5 + Eio)は Linux で domain ごとに io_uring の ring を開き、kernel は ring の記憶を uid ごとの
RLIMIT_MEMLOCK で数える — 同じ uid の process の全部で 1 つの予算(agent の Pod では 8 MiB)。同じ機体で同じ uid の semgrep が
重なると次の ring が `io_uring_queue_init` の ENOMEM で作れず、semgrep は「semgrep-core rule validation failed」で所見なしに
落ちる(2026-10-16 の根の全体の走りで tests/semgrep/test_public_api_subpackage_imports_rule.py が赤・再走で緑)。
予算が尽きた形を、子の RLIMIT_MEMLOCK を 16 KiB に下げて作る(機体の残りに依らず毎回尽きる)。
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
    message: 壁の時計で眠る
    pattern: time.sleep(...)
"""
TARGET = "import time\n\ntime.sleep(1)\n"
# 子の sh で locked memory の上限を下げてから semgrep に替わる(ulimit は sh の組み込み — 下げるのに権限は要らない)
SPENT_BUDGET = 'ulimit -l 16 && exec "$@"'


def test_semgrep_answers_when_the_locked_memory_budget_is_spent(
    tmp_path: Path, per_test_deadline: float | None
) -> None:
    semgrep = shutil.which("semgrep")
    assert semgrep is not None, "semgrep は dev 依存に在る — `make sync` を実行すること"
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
    # io_uring を使う backend では、ここで exit 2・stdout は JSON でない(「semgrep-core rule validation failed」)
    assert completed.returncode in {0, 1}, (
        f"semgrep が io_uring の予算の尽きで落ちた(exit {completed.returncode}):\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    payload = json.loads(completed.stdout)
    assert payload["errors"] == []
    assert [finding["start"]["line"] for finding in payload["results"]] == [3]
