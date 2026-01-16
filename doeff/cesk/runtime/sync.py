"""Synchronous runtime for CESK machine execution."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff.cesk.runtime.base import BaseRuntime

if TYPE_CHECKING:
    from doeff.cesk.handlers import Handler
    from doeff.program import Program


class SyncRuntime(BaseRuntime):

    def run(
        self,
        program: Program,
        env: dict[str, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> Any:
        from doeff.cesk.state import CESKState

        state = CESKState.initial(program, env, store)
        return self.step_until_done(state)


__all__ = ["SyncRuntime"]
