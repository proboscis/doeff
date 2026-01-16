"""Simulation runtime with deterministic time control."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from doeff.cesk.runtime.base import BaseRuntime

if TYPE_CHECKING:
    from doeff.cesk.handlers import Handler
    from doeff.program import Program


class SimulationRuntime(BaseRuntime):

    def __init__(self, handlers: dict[type, Handler] | None = None, start_time: datetime | None = None):
        super().__init__(handlers)
        self._current_time = start_time if start_time is not None else datetime.now()

    def run(
        self,
        program: Program,
        env: dict[str, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> Any:
        from doeff.cesk.state import CESKState

        if store is None:
            store = {}

        store["__current_time__"] = self._current_time

        state = CESKState.initial(program, env, store)
        return self.step_until_done(state)


__all__ = ["SimulationRuntime"]
