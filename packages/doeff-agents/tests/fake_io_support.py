"""検の I/O 家(段 7 lane 7c)。

driver 層の program は `doeff_agents.io_effects` の要求しか出さないので、
検は実 file・実 process・実 socket を 1 つも触らずに、記憶の中の世界で
同じ program を回せる。ここはその composition root を組む助けだけを持つ。
"""

from __future__ import annotations

from collections.abc import Callable
from subprocess import CompletedProcess

import hy  # noqa: F401  # .hy import hook — the fake handler is a Hy module
from doeff import Program
from doeff_agents.io_effects import ProcessOutcome
from doeff_agents.io_fake import FakeIoWorld, run_fake_io
from doeff_agents.io_root import IoRoot

__all__ = [
    "FakeIoWorld",
    "ProcessOutcome",
    "completed_process_script",
    "fake_io_root",
    "ok_outcome",
]


def fake_io_root(world: FakeIoWorld) -> IoRoot:
    """Return the composition root that runs programs in ``world``."""

    def root(program: Program) -> object:
        return run_fake_io(world, program)

    return root


def ok_outcome(stdout: str = "", stderr: str = "") -> ProcessOutcome:
    """A successful child-process outcome."""
    return ProcessOutcome(exit_code=0, stdout=stdout, stderr=stderr, timed_out=False)


def completed_process_script(
    fake_run: Callable[[list[str]], CompletedProcess[str]],
) -> Callable[[tuple[str, ...]], ProcessOutcome]:
    """``subprocess.CompletedProcess`` を返す台本を子 process の答えへ写す。

    旧い検が持っていた ``fake_run(args, **kwargs)`` の形をそのまま使えるようにする
    ための薄い写しで、判断は 1 つも足さない。
    """

    def script(argv: tuple[str, ...]) -> ProcessOutcome:
        completed = fake_run(list(argv))
        return ProcessOutcome(
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            timed_out=False,
        )

    return script
