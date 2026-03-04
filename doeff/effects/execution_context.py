"""Execution-context introspection effects."""

from dataclasses import dataclass

import doeff_vm

from doeff.trace import ActiveChainEntry, coerce_active_chain_entries
from doeff.traceback import build_doeff_traceback

GetExecutionContext = doeff_vm.GetExecutionContext
ExecutionContext = doeff_vm.ExecutionContext


@dataclass(frozen=True)
class ActiveChainSnapshot:
    entries: tuple[ActiveChainEntry, ...]

    @classmethod
    def from_execution_context(cls, context: object) -> "ActiveChainSnapshot":
        active_chain = getattr(context, "active_chain", ())
        if not isinstance(active_chain, (list, tuple)):
            active_chain = ()
        entries = coerce_active_chain_entries(list(active_chain))
        return cls(entries=tuple(entries))

    def format_default(self, *, exception: BaseException | None = None) -> str:
        error = exception or RuntimeError("Active execution context snapshot")
        traceback = build_doeff_traceback(
            error,
            trace_entries=[],
            active_chain_entries=self.entries,
            allow_active=True,
        )
        return traceback.format_default()


def snapshot_active_chain(context: object) -> ActiveChainSnapshot:
    return ActiveChainSnapshot.from_execution_context(context)


__all__ = [
    "ActiveChainSnapshot",
    "ExecutionContext",
    "GetExecutionContext",
    "snapshot_active_chain",
]
