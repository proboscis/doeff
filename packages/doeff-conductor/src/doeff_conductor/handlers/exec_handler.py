"""Handler for deterministic gate command execution."""

import os
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from doeff_conductor.types import ExecResult

if TYPE_CHECKING:
    from doeff_conductor.effects.exec import Exec
    from doeff_conductor.types import Workspace


WorkspaceResolver = Callable[["Workspace"], Path]


class ExecHandler:
    """Run deterministic commands and tee full output to log files."""

    def __init__(
        self,
        *,
        workspace_resolver: WorkspaceResolver | None = None,
        log_dir: Path | None = None,
    ) -> None:
        self._workspace_resolver = workspace_resolver
        default_log_dir = Path(tempfile.gettempdir()) / "doeff-conductor-exec"
        self.log_dir = log_dir or default_log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._counter = 0

    def _next_log_path(self) -> Path:
        self._counter += 1
        return self.log_dir / f"exec-{os.getpid()}-{self._counter:04d}.log"

    def _resolve_workdir(self, effect: "Exec") -> Path:
        if effect.workspace is not None:
            if self._workspace_resolver is None:
                raise ValueError("Exec workspace requires a workspace resolver")
            return self._workspace_resolver(effect.workspace)
        if effect.workdir is not None:
            return effect.workdir
        raise ValueError("Exec requires either workdir or workspace")

    def handle_exec(self, effect: "Exec") -> ExecResult:
        """Run the command and return a structured result."""
        workdir: Path = self._resolve_workdir(effect)
        log_path: Path = self._next_log_path()

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

