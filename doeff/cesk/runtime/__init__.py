"""Runtime implementations for the unified CESK architecture."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, TypeVar

if TYPE_CHECKING:
    from doeff.cesk.handlers import HandlerRegistry
    from doeff.cesk.state import CESKState
    from doeff.cesk.types import Environment, Store
    from doeff.program import Program

T = TypeVar("T")


class Runtime(Protocol[T]):
    def run(
        self,
        program: Program[T],
        env: Environment | dict[Any, Any] | None = None,
        store: Store | None = None,
    ) -> T:
        ...


from doeff.cesk.runtime.simulation import SimulationRuntimeError, UnifiedSimulationRuntime
from doeff.cesk.runtime.sync import SyncRuntimeError, UnifiedSyncRuntime


__all__ = [
    "Runtime",
    "SimulationRuntimeError",
    "SyncRuntimeError",
    "UnifiedSimulationRuntime",
    "UnifiedSyncRuntime",
]
