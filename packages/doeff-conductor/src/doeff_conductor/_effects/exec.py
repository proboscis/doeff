"""Deterministic command execution effects for conductor gates."""

from dataclasses import dataclass
from pathlib import Path

from doeff_conductor._effects.base import ConductorEffectBase
from doeff_conductor._types import ExecResult, Workspace


@dataclass(frozen=True, kw_only=True)
class Exec(ConductorEffectBase[ExecResult]):
    """Run a deterministic command and tee its full output to a log file.

    Yields: ExecResult
    """

    cmd: str
    workdir: Path | None = None
    workspace: Workspace | None = None
    timeout: float | None = None
