"""Runtime package for CESK execution.

This module provides runtime implementations that coordinate CESK machine execution:
- Runtime protocol defining the interface
- BaseRuntime with common scheduling logic
- SimulationRuntime for testing with controlled time
- AsyncioRuntime for async I/O
- SyncRuntime for synchronous execution

The runtime is responsible for:
- Scheduling tasks (which task to step next)
- Executing I/O operations
- Managing time (real or simulated)
- Coordinating futures
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from doeff.cesk.state import CESKState, TaskStatus
from doeff.cesk.types import TaskId

if TYPE_CHECKING:
    from doeff.cesk.handlers import HandlerRegistry
    from doeff.cesk.step import StepOutput
    from doeff.program import Program


@runtime_checkable
class Runtime(Protocol):
    """Protocol for CESK runtime implementations.

    A runtime coordinates the execution of the CESK machine:
    - Chooses which task to step next (scheduling)
    - Handles I/O events (IORequested, AwaitRequested)
    - Manages time (real or simulated)
    - Resolves futures and wakes waiting tasks

    Runtimes receive events from step() and react to them.
    """

    def run(self, program: Program, env: dict | None = None) -> Any:
        """Run a program to completion.

        Args:
            program: The program to execute
            env: Optional initial environment

        Returns:
            The final result value

        Raises:
            Exception if the program fails
        """
        ...

    def step_until_done(self, state: CESKState, handlers: HandlerRegistry) -> CESKState:
        """Step all tasks until no more can make progress.

        Args:
            state: Initial CESK state
            handlers: Handler registry for effects

        Returns:
            Final state after all tasks complete or block
        """
        ...


from doeff.cesk.runtime.base import BaseRuntime
from doeff.cesk.runtime.simulation import SimulationRuntime, TimerEntry
from doeff.cesk.runtime.sync import SyncRuntime
from doeff.cesk.runtime.asyncio_runtime import AsyncioRuntime

__all__ = [
    "Runtime",
    "BaseRuntime",
    "SimulationRuntime",
    "TimerEntry",
    "SyncRuntime",
    "AsyncioRuntime",
]
