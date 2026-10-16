"""doeff run は Hy を入れていない核の環境(doeff・doeff-vm・doeff-core-effects・frozendict)で動く。

子の process に Hy の import だけを塞ぐ finder を入れてから CLI の入口を呼ぶ。Hy が要るのは --hy と .hy の program だけで、
--help・--program(Python の program)・-c・--profile・DOEFF_PROFILE は Hy 無しで通る。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.cli.cli_child import with_settings

pytestmark = pytest.mark.cli

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHILD_DEADLINE_SECONDS = 60

# Hy の import に ModuleNotFoundError を返す finder を最前に置いてから doeff の入口を呼ぶ子の process の code。
# 塞ぐ前に Hy が読まれていると塞いだことにならないので、その時は 99 で終える。
BLOCK_HY_THEN_RUN_CLI = """
import sys

_HY_ROOTS = ("hy", "hyrule", "doeff_hy", "doeff_hy_pytest")

if any(name.split(".")[0] in _HY_ROOTS for name in sys.modules):
    sys.exit(99)


class _NoHy:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in _HY_ROOTS:
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)


sys.meta_path.insert(0, _NoHy())

from doeff.__main__ import main

sys.exit(main(sys.argv[1:]))
"""

NOHY_PROGRAM = """
from doeff import do
from doeff_core_effects import Get, Put


@do
def answer():
    yield Put("x", 1)
    return (yield Get("x")) + 41


answer_program = answer()
"""

CODE = 'from doeff_core_effects import Get, Put\nyield Put("x", 1)\n(yield Get("x")) + 41'
PROFILE_LINE = "[DOEFF][PROFILE] Profiling enabled"


@pytest.mark.parametrize(
    ("argv", "settings", "stdout_has", "profile_line"),
    [
        (["--help"], {}, "usage: doeff", False),
        (["run", "--help"], {}, "usage: doeff run", False),
        (["run", "--no-runbox", "--program", "nohy_program.answer_program"], {}, "42\n", False),
        (["run", "--no-runbox", "-c", CODE], {}, "42\n", False),
        (["run", "--no-runbox", "-c", CODE, "--profile"], {}, "42\n", True),
        (["run", "--no-runbox", "-c", CODE], {"DOEFF_PROFILE": "1"}, "42\n", True),
        (["run", "--no-runbox", "-c", CODE], {"DOEFF_PROFILE": "0"}, "42\n", False),
    ],
    ids=["help", "run-help", "program", "code", "profile-flag", "profile-env-1", "profile-env-0"],
)
def test_doeff_run_works_without_hy(
    tmp_path: Path,
    deadline_scale_factor: float,
    argv: list[str],
    settings: dict[str, str],
    stdout_has: str,
    profile_line: bool,
) -> None:
    (tmp_path / "nohy_program.py").write_text(NOHY_PROGRAM)
    command = with_settings(
        [sys.executable, "-c", BLOCK_HY_THEN_RUN_CLI, *argv],
        {"PYTHONPATH": str(tmp_path), "DOEFF_PROFILE": None, **settings},
    )

    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS * deadline_scale_factor,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert stdout_has in completed.stdout
    assert (PROFILE_LINE in completed.stderr) is profile_line
