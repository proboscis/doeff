"""Synchronous single-threaded runtime for the unified CESK architecture."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff.cesk.runtime.base import BaseRuntime
from doeff.cesk.handlers import Handler

if TYPE_CHECKING:
    from doeff.program import Program


class SyncRuntime(BaseRuntime):
    def __init__(self, handlers: dict[type, Handler] | None = None):
        super().__init__(handlers)

    def run(
        self,
        program: Program,
        env: dict[str, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> Any:
        state = self._create_initial_state(program, env, store)
        return self._step_until_done(state)


__all__ = [
    "SyncRuntime",
]
