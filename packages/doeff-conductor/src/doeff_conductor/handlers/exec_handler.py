"""Handler for deterministic gate command execution."""

import itertools
import os
import subprocess
import tempfile
from collections.abc import Generator, Iterator
from pathlib import Path
from typing import Any

from doeff import EffectBase, K, Resume, do, handler
from doeff.program import ProgramHandler
from doeff_conductor.effects.exec import Exec
from doeff_conductor.handlers._workspace_path import WorkspacePath
from doeff_conductor.types import ExecResult

__all__ = ["exec_handler"]


@do
def _workdir(effect: Exec) -> Generator[EffectBase, Any, Path]:
    """The directory the command runs in: the workspace's worktree, or the given workdir."""
    if effect.workspace is not None:
        return (yield WorkspacePath(workspace=effect.workspace))
    if effect.workdir is not None:
        return effect.workdir
    raise ValueError("Exec requires either workdir or workspace")


def _run_command(effect: Exec, workdir: Path, log_path: Path) -> ExecResult:
    """Run the command, tee its whole output to ``log_path`` and return a structured result."""
    process = subprocess.Popen(
        effect.cmd,
        cwd=workdir,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    output: str = ""
    timed_out = False
    try:
        output, _ = process.communicate(timeout=effect.timeout)
    except subprocess.TimeoutExpired as timeout_error:
        timed_out = True
        process.kill()
        remaining_output, _ = process.communicate()
        partial_output = timeout_error.output if isinstance(timeout_error.output, str) else ""
        output = partial_output + remaining_output
        output += f"\n[doeff-conductor] command timed out after {effect.timeout}s\n"

    with log_path.open("w", encoding="utf-8") as log_file:
        log_file.write(output)

    exit_code = process.returncode
    if timed_out:
        exit_code = 124
    return ExecResult(
        exit_code=exit_code,
        log_path=str(log_path),
        output=output,
        timed_out=timed_out,
    )


def exec_handler(*, log_dir: Path | None = None) -> ProgramHandler:
    """Build the handler that runs deterministic gate commands and tees their output to log files.

    A command on a workspace runs in its worktree, so the handler is installed inside the
    workspace handler (``WorkspacePath``).

    Args:
        log_dir: Where the per-command log files go. A directory under the temp dir if not provided.
    """
    active_log_dir: Path = log_dir or Path(tempfile.gettempdir()) / "doeff-conductor-exec"
    active_log_dir.mkdir(parents=True, exist_ok=True)
    log_numbers: Iterator[int] = itertools.count(1)

    @handler
    def answer_exec(effect: Exec, k: K):
        """Run one gate command and answer its result."""
        workdir: Path = yield _workdir(effect)
        log_path: Path = active_log_dir / f"exec-{os.getpid()}-{next(log_numbers):04d}.log"
        return (yield Resume(k, _run_command(effect, workdir, log_path)))

    return answer_exec
