"""Drive a conductor handler effect by effect, for tests that check what it answers."""

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from doeff_conductor._handlers._workspace_path import WorkspacePath
from doeff_conductor import Workspace
from doeff_core_effects import scheduled

from doeff import EffectBase, K, Resume, do, handler, run
from doeff.program import ProgramHandler

Answer = Callable[[Any], Any]
"""What ``answering`` returns: run one effect, return its answer."""


@do
def perform(effect: EffectBase):
    """The program that performs one effect and returns its answer."""
    return (yield effect)


def answering(handlers: ProgramHandler, *, under_scheduler: bool = False) -> Answer:
    """A function that runs one effect under ``handlers`` and returns the answer (or raises).

    The handlers keep their state between calls, as they do across the effects of one program.
    ``under_scheduler`` runs each effect under ``scheduled``, for handlers that wait off thread.
    """

    def answer(effect: EffectBase) -> Any:
        program = handlers(perform(effect))
        return run(scheduled(program) if under_scheduler else program)

    return answer


def workspace_paths(paths: Mapping[str, Path]) -> ProgramHandler:
    """A workspace handler stand-in that only says where each workspace id is materialized."""

    @handler
    def answer_path(effect: WorkspacePath, k: K):
        workspace: Workspace = effect.workspace
        return (yield Resume(k, paths[workspace.id]))

    return answer_path
