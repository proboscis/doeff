"""Deterministic command execution effects for conductor gates."""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .base import ConductorEffectBase

if TYPE_CHECKING:
    from doeff_conductor.types import Workspace


@dataclass(frozen=True, kw_only=True)
class Exec(ConductorEffectBase):
    """Run a deterministic command and tee its full output to a log file.

    Yields: ExecResult
    """

    cmd: str
    workdir: Path | None = None
    workspace: "Workspace | None" = None
    timeout: float | None = None

