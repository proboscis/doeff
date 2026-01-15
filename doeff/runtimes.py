"""
Runtime variants for doeff programs.

This module provides the primary entry points for running doeff programs:
- AsyncioRuntime: For async execution (await runtime.run(program))
- SyncRuntime: For synchronous execution (runtime.run(program))
- SimulationRuntime: For simulation with virtual time

These replace the deprecated doeff.cesk.run() and run_sync() functions.

Example:
    >>> from doeff import do, Program, Get, Put
    >>> from doeff.runtimes import AsyncioRuntime, SyncRuntime
    >>>
    >>> @do
    >>> def example():
    ...     yield Put("x", 42)
    ...     return (yield Get("x"))
    >>>
    >>> # Async execution
    >>> value = await AsyncioRuntime().run(example())
    >>>
    >>> # Sync execution
    >>> value = SyncRuntime().run(example())
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from doeff.runtime import (
    AsyncioScheduler,
    FIFOScheduler,
    RuntimeResult,
    ScheduledHandlers,
    Scheduler,
    SimulationScheduler,
)

if TYPE_CHECKING:
    from doeff.cesk import Environment, Store
    from doeff.program import Program

T = TypeVar("T")


class AsyncioRuntime:
    """Runtime for async execution of doeff programs.
    
    Uses asyncio for scheduling and awaiting async operations.
    This is the recommended runtime for most async applications.
    
    Example:
        >>> runtime = AsyncioRuntime()
        >>> result = await runtime.run(my_program())
        >>> print(result.value)
    """
    
    def __init__(
        self,
        scheduler: Scheduler | None = None,
        handlers: ScheduledHandlers | None = None,
    ):
        """Initialize the AsyncioRuntime.
        
        Args:
            scheduler: Custom scheduler. Defaults to AsyncioScheduler.
            handlers: Custom effect handlers.
        """
        self._scheduler = scheduler if scheduler is not None else AsyncioScheduler()
        self._handlers = handlers
    
    async def run(
        self,
        program: "Program[T]",
        env: "Environment | dict | None" = None,
        store: "Store | None" = None,
    ) -> T:
        """Run a program asynchronously and return its value.
        
        Args:
            program: The doeff program to execute.
            env: Initial environment (reader context).
            store: Initial store (state).
            
        Returns:
            The program's result value.
            
        Raises:
            Exception: Re-raises any exception from program execution.
        """
        from doeff.cesk.run import _run_internal
        from doeff._vendor import FrozenDict
        from doeff.cesk.dispatcher import ScheduledEffectDispatcher
        from doeff.scheduled_handlers import default_scheduled_handlers
        
        if env is None:
            E = FrozenDict()
        elif isinstance(env, FrozenDict):
            E = env
        else:
            E = FrozenDict(env)
        
        S = store if store is not None else {}
        
        dispatcher = ScheduledEffectDispatcher(
            user_handlers=self._handlers,
            builtin_handlers=default_scheduled_handlers(),
        )
        
        result, _, captured_traceback = await _run_internal(
            program, E, S, dispatcher=dispatcher, scheduler=self._scheduler
        )
        
        if result.is_ok():
            return result.ok()  # type: ignore[return-value]
        else:
            error = result.err()
            if error is not None:
                raise error
            raise RuntimeError("Program execution failed with unknown error")
    
    async def run_result(
        self,
        program: "Program[T]",
        env: "Environment | dict | None" = None,
        store: "Store | None" = None,
    ) -> RuntimeResult[T]:
        """Run a program and return a RuntimeResult with full details.
        
        Args:
            program: The doeff program to execute.
            env: Initial environment (reader context).
            store: Initial store (state).
            
        Returns:
            RuntimeResult containing result and metadata.
        """
        from doeff.cesk.run import _run_internal
        from doeff._vendor import FrozenDict
        from doeff.cesk.dispatcher import ScheduledEffectDispatcher
        from doeff.scheduled_handlers import default_scheduled_handlers
        
        if env is None:
            E = FrozenDict()
        elif isinstance(env, FrozenDict):
            E = env
        else:
            E = FrozenDict(env)
        
        S = store if store is not None else {}
        
        dispatcher = ScheduledEffectDispatcher(
            user_handlers=self._handlers,
            builtin_handlers=default_scheduled_handlers(),
        )
        
        result, _, captured_traceback = await _run_internal(
            program, E, S, dispatcher=dispatcher, scheduler=self._scheduler
        )
        
        return RuntimeResult(result, captured_traceback)


class SyncRuntime:
    """Runtime for synchronous execution of doeff programs.
    
    Uses asyncio.run() internally to execute async effects synchronously.
    This is useful for scripts and applications that don't use async/await.
    
    Example:
        >>> runtime = SyncRuntime()
        >>> result = runtime.run(my_program())
        >>> print(result)
    """
    
    def __init__(
        self,
        scheduler: Scheduler | None = None,
        handlers: ScheduledHandlers | None = None,
    ):
        """Initialize the SyncRuntime.
        
        Args:
            scheduler: Custom scheduler. Defaults to FIFOScheduler.
            handlers: Custom effect handlers.
        """
        self._scheduler = scheduler if scheduler is not None else FIFOScheduler()
        self._handlers = handlers
    
    def run(
        self,
        program: "Program[T]",
        env: "Environment | dict | None" = None,
        store: "Store | None" = None,
    ) -> T:
        """Run a program synchronously and return its value.
        
        Args:
            program: The doeff program to execute.
            env: Initial environment (reader context).
            store: Initial store (state).
            
        Returns:
            The program's result value.
            
        Raises:
            Exception: Re-raises any exception from program execution.
        """
        async_runtime = AsyncioRuntime(
            scheduler=self._scheduler,
            handlers=self._handlers,
        )
        return asyncio.run(async_runtime.run(program, env, store))
    
    def run_result(
        self,
        program: "Program[T]",
        env: "Environment | dict | None" = None,
        store: "Store | None" = None,
    ) -> RuntimeResult[T]:
        """Run a program and return a RuntimeResult with full details.
        
        Args:
            program: The doeff program to execute.
            env: Initial environment (reader context).
            store: Initial store (state).
            
        Returns:
            RuntimeResult containing result and metadata.
        """
        async_runtime = AsyncioRuntime(
            scheduler=self._scheduler,
            handlers=self._handlers,
        )
        return asyncio.run(async_runtime.run_result(program, env, store))


class SimulationRuntime:
    """Runtime for simulation with virtual time.
    
    Uses SimulationScheduler to advance time instantly without real delays.
    This is useful for testing time-dependent logic and simulations.
    
    Example:
        >>> runtime = SimulationRuntime()
        >>> result = await runtime.run(my_time_dependent_program())
        >>> print(f"Simulation ended at: {runtime.current_time}")
    """
    
    def __init__(
        self,
        start_time: datetime | None = None,
        handlers: ScheduledHandlers | None = None,
    ):
        """Initialize the SimulationRuntime.
        
        Args:
            start_time: Initial simulation time. Defaults to current time.
            handlers: Custom effect handlers.
        """
        self._scheduler = SimulationScheduler(start_time)
        self._handlers = handlers
    
    @property
    def current_time(self) -> datetime:
        """Get the current simulation time."""
        return self._scheduler.current_time
    
    async def run(
        self,
        program: "Program[T]",
        env: "Environment | dict | None" = None,
        store: "Store | None" = None,
    ) -> T:
        """Run a program in simulation mode and return its value.
        
        Args:
            program: The doeff program to execute.
            env: Initial environment (reader context).
            store: Initial store (state).
            
        Returns:
            The program's result value.
            
        Raises:
            Exception: Re-raises any exception from program execution.
        """
        from doeff.cesk.run import _run_internal
        from doeff._vendor import FrozenDict
        from doeff.cesk.dispatcher import ScheduledEffectDispatcher
        from doeff.scheduled_handlers import default_scheduled_handlers
        
        if env is None:
            E = FrozenDict()
        elif isinstance(env, FrozenDict):
            E = env
        else:
            E = FrozenDict(env)
        
        S = store if store is not None else {}
        
        dispatcher = ScheduledEffectDispatcher(
            user_handlers=self._handlers,
            builtin_handlers=default_scheduled_handlers(),
        )
        
        result, _, captured_traceback = await _run_internal(
            program, E, S, dispatcher=dispatcher, scheduler=self._scheduler
        )
        
        if result.is_ok():
            return result.ok()  # type: ignore[return-value]
        else:
            error = result.err()
            if error is not None:
                raise error
            raise RuntimeError("Program execution failed with unknown error")
    
    async def run_result(
        self,
        program: "Program[T]",
        env: "Environment | dict | None" = None,
        store: "Store | None" = None,
    ) -> RuntimeResult[T]:
        """Run a program and return a RuntimeResult with full details.
        
        Args:
            program: The doeff program to execute.
            env: Initial environment (reader context).
            store: Initial store (state).
            
        Returns:
            RuntimeResult containing result and metadata.
        """
        from doeff.cesk.run import _run_internal
        from doeff._vendor import FrozenDict
        from doeff.cesk.dispatcher import ScheduledEffectDispatcher
        from doeff.scheduled_handlers import default_scheduled_handlers
        
        if env is None:
            E = FrozenDict()
        elif isinstance(env, FrozenDict):
            E = env
        else:
            E = FrozenDict(env)
        
        S = store if store is not None else {}
        
        dispatcher = ScheduledEffectDispatcher(
            user_handlers=self._handlers,
            builtin_handlers=default_scheduled_handlers(),
        )
        
        result, _, captured_traceback = await _run_internal(
            program, E, S, dispatcher=dispatcher, scheduler=self._scheduler
        )
        
        return RuntimeResult(result, captured_traceback)
    
    def run_sync(
        self,
        program: "Program[T]",
        env: "Environment | dict | None" = None,
        store: "Store | None" = None,
    ) -> T:
        """Run a program synchronously in simulation mode.
        
        Args:
            program: The doeff program to execute.
            env: Initial environment (reader context).
            store: Initial store (state).
            
        Returns:
            The program's result value.
        """
        return asyncio.run(self.run(program, env, store))


__all__ = [
    "AsyncioRuntime",
    "SyncRuntime",
    "SimulationRuntime",
]
