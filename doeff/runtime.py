"""
doeff Runtime Model: Single-shot Algebraic Effects with Pluggable Scheduler.

This module implements the runtime architecture based on single-shot algebraic effects
with a pluggable scheduler as specified in ISSUE-CORE-432.

Key abstractions:
- Continuation: Single-shot suspended computation
- Scheduler: Manages pool of continuations with pluggable policy
- EffectHandler: Receives continuation + scheduler, decides how to proceed
- HandlerResult: Resume | Suspend | Scheduled

The runtime provides:
- User-defined effects (language features are not fixed)
- Pluggable scheduling policies (simulation, realtime, priority, actor, etc.)
- Same program runs in different modes by swapping scheduler
"""

from __future__ import annotations

import asyncio
import heapq
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Generic, Protocol, TypeVar

if TYPE_CHECKING:
    from doeff.cesk import CESKState, Environment, Store
    from doeff.program import Program
    from doeff._types_internal import EffectBase


T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)


# ============================================================================
# Handler Result Types
# ============================================================================


@dataclass(frozen=True)
class Resume:
    """Resume current continuation immediately with value.
    
    Used when the handler can compute the result synchronously
    without needing to wait or schedule work.
    """
    value: Any
    store: "Store"


@dataclass(frozen=True)
class Suspend:
    """Await external async operation, then resume.
    
    Used when the handler needs to perform async I/O.
    The runtime will await the awaitable and resume the
    continuation with the result.
    """
    awaitable: Awaitable[Any]
    store: "Store"


@dataclass(frozen=True)
class Scheduled:
    """Continuation was submitted to scheduler, pick next.
    
    Used when the handler has added the continuation to the
    scheduler for later resumption (e.g., time-based delay).
    The runtime should pick the next continuation from scheduler.
    """
    store: "Store"


HandlerResult = Resume | Suspend | Scheduled


# ============================================================================
# Continuation (Single-shot, opaque)
# ============================================================================


@dataclass
class Continuation:
    """Suspended computation that can be resumed once (single-shot).
    
    Continuations encapsulate the state needed to resume a computation:
    - The resume callback for success
    - The resume_error callback for errors
    - The CESK state components (E, S, K) at suspension time
    
    Single-shot constraint: Python generators cannot be cloned, so once
    resumed, the continuation cannot be used again.
    """
    
    # Continuation callbacks (from Suspended result type)
    _resume: Callable[[Any, "Store"], "CESKState"]
    _resume_error: Callable[[BaseException], "CESKState"]
    
    # CESK state at suspension time
    env: "Environment"
    store: "Store"
    
    # Track if continuation has been used (single-shot enforcement)
    _used: bool = field(default=False, repr=False)
    
    def resume(self, value: Any, store: "Store") -> "CESKState":
        """Resume with a value.
        
        Args:
            value: The result value to send to the computation.
            store: The updated store.
            
        Returns:
            The next CESKState for continued execution.
            
        Raises:
            RuntimeError: If continuation has already been used.
        """
        if self._used:
            raise RuntimeError("Continuation already used (single-shot)")
        self._used = True
        return self._resume(value, store)
    
    def resume_error(self, ex: BaseException, store: "Store") -> "CESKState":
        """Resume with an error.
        
        Args:
            ex: The exception to throw into the computation.
            store: The store (typically unchanged on error).
            
        Returns:
            The next CESKState for continued execution.
            
        Raises:
            RuntimeError: If continuation has already been used.
        """
        if self._used:
            raise RuntimeError("Continuation already used (single-shot)")
        self._used = True
        return self._resume_error(ex)
    
    @classmethod
    def from_program(
        cls,
        program: "Program",
        env: "Environment",
        store: "Store",
    ) -> "Continuation":
        """Create a continuation from a program.
        
        This creates an initial continuation that, when resumed,
        will start executing the program.
        """
        from doeff.cesk import CESKState, ProgramControl
        
        def resume(value: Any, new_store: "Store") -> "CESKState":
            # Initial resume starts the program
            return CESKState.initial(program, env, new_store)
        
        def resume_error(ex: BaseException) -> "CESKState":
            from doeff.cesk import Error
            return CESKState(
                C=Error(ex),
                E=env,
                S=store,
                K=[],
            )
        
        return cls(
            _resume=resume,
            _resume_error=resume_error,
            env=env,
            store=store,
        )


# ============================================================================
# Scheduler Protocol
# ============================================================================


class Scheduler(Protocol):
    """Manages pool of continuations with pluggable scheduling policy.
    
    The Scheduler protocol is minimal by design:
    - submit(): Add a continuation to the pool
    - next(): Pick the next continuation to run
    
    The `hint` parameter is opaque - each scheduler interprets it differently:
    - FIFOScheduler: ignores hint
    - PriorityScheduler: hint = priority (int)
    - SimulationScheduler: hint = timestamp
    - ActorScheduler: hint = (actor_id, message)
    """
    
    def submit(self, k: Continuation, hint: Any = None) -> None:
        """Add continuation to pool.
        
        Args:
            k: The continuation to schedule.
            hint: Scheduler-specific metadata (e.g., priority, timestamp).
        """
        ...
    
    def next(self) -> Continuation | None:
        """Pick next continuation to run.
        
        Returns:
            The next continuation to execute, or None if done.
        """
        ...


# ============================================================================
# Effect Handler Protocol (new unified protocol)
# ============================================================================


class ScheduledEffectHandler(Protocol):
    """User-defined handler that receives continuation and scheduler.
    
    This is the new unified handler protocol that replaces the separate
    SyncEffectHandler and AsyncEffectHandler protocols.
    
    Handlers can:
    - Resume k immediately: return Resume(value, store)
    - Store k in scheduler for later: scheduler.submit(k, hint); return Scheduled(store)
    - Await external async: return Suspend(awaitable, store)
    - Create new continuation: scheduler.submit(new_k); return Resume(None, store)
    
    The handler signature provides explicit access to:
    - effect: The effect instance to handle
    - env: Read-only environment
    - store: Current store (immutable - return new store)
    - k: Continuation for later resumption
    - scheduler: For scheduling work
    """
    
    def __call__(
        self,
        effect: "EffectBase",
        env: "Environment",
        store: "Store",
        k: Continuation,
        scheduler: Scheduler,
    ) -> HandlerResult:
        """Handle an effect.
        
        Args:
            effect: The effect instance to handle.
            env: Read-only environment (FrozenDict).
            store: Current store (immutable semantics - return new store).
            k: Continuation to resume after handling.
            scheduler: Scheduler for managing continuations.
            
        Returns:
            HandlerResult indicating how to proceed.
        """
        ...


# Type alias for handler registry
ScheduledHandlers = dict[type["EffectBase"], ScheduledEffectHandler]


# ============================================================================
# Reference Schedulers
# ============================================================================


class FIFOScheduler:
    """First-In-First-Out scheduler (simplest).
    
    Continuations are executed in the order they were submitted.
    Useful for simple sequential execution and testing.
    """
    
    def __init__(self) -> None:
        self._queue: deque[Continuation] = deque()
    
    def submit(self, k: Continuation, hint: Any = None) -> None:
        """Add continuation to queue (hint ignored)."""
        self._queue.append(k)
    
    def next(self) -> Continuation | None:
        """Get next continuation from queue."""
        return self._queue.popleft() if self._queue else None
    
    def __len__(self) -> int:
        """Number of pending continuations."""
        return len(self._queue)


class PriorityScheduler:
    """Priority-based scheduler.
    
    Continuations with lower priority values are executed first.
    Uses sequence counter for deterministic FIFO tie-breaking.
    """
    
    def __init__(self) -> None:
        self._queue: list[tuple[int, int, Continuation]] = []
        self._seq: int = 0
    
    def submit(self, k: Continuation, hint: Any = None) -> None:
        """Add continuation with priority (hint = priority, default 0)."""
        priority = hint if hint is not None else 0
        heapq.heappush(self._queue, (priority, self._seq, k))
        self._seq += 1
    
    def next(self) -> Continuation | None:
        """Get highest-priority continuation."""
        if self._queue:
            _, _, k = heapq.heappop(self._queue)
            return k
        return None
    
    def __len__(self) -> int:
        """Number of pending continuations."""
        return len(self._queue)


class SimulationScheduler:
    """Simulation scheduler with time-based scheduling.
    
    Manages two pools:
    - ready: LIFO stack for immediately runnable continuations
    - timed: Priority queue for time-scheduled continuations
    
    Time advances discretely - when ready is empty, the scheduler
    pops from timed and advances current_time.
    
    This is compatible with proboscis-ema's simulation semantics.
    """
    
    def __init__(self, start_time: datetime | None = None) -> None:
        self._ready: list[Continuation] = []  # LIFO stack
        self._timed: list[tuple[datetime, int, Continuation]] = []  # heapq
        self._current_time: datetime = start_time or datetime.now()
        self._seq: int = 0
    
    @property
    def current_time(self) -> datetime:
        """Current simulation time."""
        return self._current_time
    
    def submit(self, k: Continuation, hint: Any = None) -> None:
        """Add continuation (hint = None for ready, datetime for timed)."""
        if hint is None:
            # Ready immediately - push to stack (LIFO for depth-first)
            self._ready.append(k)
        else:
            # Time-scheduled
            if isinstance(hint, timedelta):
                target_time = self._current_time + hint
            elif isinstance(hint, datetime):
                target_time = hint
            else:
                raise TypeError(f"SimulationScheduler hint must be datetime or timedelta, got {type(hint)}")
            heapq.heappush(self._timed, (target_time, self._seq, k))
            self._seq += 1
    
    def next(self) -> Continuation | None:
        """Get next continuation, advancing time if needed."""
        if self._ready:
            return self._ready.pop()  # LIFO - depth first
        elif self._timed:
            time, _, k = heapq.heappop(self._timed)
            self._current_time = time  # Advance simulation time
            return k
        return None
    
    def advance_time(self, delta: timedelta) -> None:
        """Manually advance simulation time."""
        self._current_time += delta
    
    def set_time(self, time: datetime) -> None:
        """Set simulation time directly."""
        self._current_time = time
    
    def __len__(self) -> int:
        """Number of pending continuations."""
        return len(self._ready) + len(self._timed)


class RealtimeScheduler:
    """Realtime scheduler using asyncio for wall-clock timing.
    
    Integrates with asyncio event loop for actual time delays.
    Useful for production systems where real timing is needed.
    """
    
    def __init__(self) -> None:
        self._ready: deque[Continuation] = deque()
        self._pending_timers: int = 0
    
    def submit(self, k: Continuation, hint: Any = None) -> None:
        """Add continuation (hint = None for ready, float for delay in seconds)."""
        if hint is None:
            self._ready.append(k)
        else:
            # Schedule with delay
            delay_seconds = float(hint)
            self._pending_timers += 1
            
            async def delayed_submit():
                await asyncio.sleep(delay_seconds)
                self._ready.append(k)
                self._pending_timers -= 1
            
            # Create task to run the delayed submit
            asyncio.create_task(delayed_submit())
    
    def next(self) -> Continuation | None:
        """Get next ready continuation."""
        return self._ready.popleft() if self._ready else None
    
    @property
    def has_pending(self) -> bool:
        """Check if there are pending timer callbacks."""
        return self._pending_timers > 0 or len(self._ready) > 0
    
    def __len__(self) -> int:
        """Number of immediately ready continuations."""
        return len(self._ready)


# ============================================================================
# Handler Registry
# ============================================================================


class ScheduledHandlerRegistry:
    """Registry of handlers for the new scheduler-based model.
    
    Supports MRO-based lookup with caching, similar to EffectDispatcher
    but for the new ScheduledEffectHandler protocol.
    """
    
    def __init__(
        self,
        handlers: ScheduledHandlers | None = None,
    ) -> None:
        self._handlers: ScheduledHandlers = handlers or {}
        self._cache: dict[type, ScheduledEffectHandler | None] = {}
    
    def register(
        self,
        effect_type: type["EffectBase"],
        handler: ScheduledEffectHandler,
    ) -> None:
        """Register a handler for an effect type."""
        self._handlers[effect_type] = handler
        # Invalidate cache
        self._cache.clear()
    
    def lookup(self, effect: "EffectBase") -> ScheduledEffectHandler | None:
        """Lookup handler for an effect using MRO fallback."""
        effect_type = type(effect)
        
        # Check cache
        if effect_type in self._cache:
            return self._cache[effect_type]
        
        # Exact match
        if effect_type in self._handlers:
            handler = self._handlers[effect_type]
            self._cache[effect_type] = handler
            return handler
        
        # MRO fallback
        for base in effect_type.__mro__[1:]:
            if base in self._handlers:
                handler = self._handlers[base]
                self._cache[effect_type] = handler
                return handler
        
        # Not found
        self._cache[effect_type] = None
        return None


# ============================================================================
# Runtime Main Loop (Scheduler-based)
# ============================================================================


async def run_with_scheduler(
    program: "Program",
    scheduler: Scheduler,
    handlers: ScheduledHandlerRegistry,
    env: "Environment | dict[Any, Any] | None" = None,
    store: "Store | None" = None,
) -> Any:
    """Run a program with a pluggable scheduler.
    
    This is the new main loop that uses the Scheduler abstraction
    instead of the single-continuation model.
    
    Args:
        program: The program to execute.
        scheduler: The scheduler to use for continuation management.
        handlers: Registry of effect handlers.
        env: Initial environment (default: empty).
        store: Initial store (default: empty).
        
    Returns:
        The final result of the program.
        
    Raises:
        Exception: If the program fails with an unhandled error.
    """
    from doeff._vendor import FrozenDict
    from doeff.cesk import (
        CESKState,
        Done,
        Error,
        Failed,
        Suspended,
        Value,
        step,
    )
    
    # Initialize environment
    if env is None:
        E = FrozenDict()
    elif isinstance(env, FrozenDict):
        E = env
    else:
        E = FrozenDict(env)
    
    # Initialize store
    S: Store = store if store is not None else {}
    
    # Create initial continuation and submit to scheduler
    initial_k = Continuation.from_program(program, E, S)
    scheduler.submit(initial_k)
    
    # Final result storage
    final_result: Any = None
    final_error: BaseException | None = None
    
    while (k := scheduler.next()) is not None:
        # Resume continuation to get initial state
        state = k.resume(None, k.store)
        
        while True:
            result = step(state)
            
            if isinstance(result, Done):
                # This continuation completed successfully
                final_result = result.value
                break
            
            if isinstance(result, Failed):
                # This continuation failed
                final_error = result.exception
                break
            
            if isinstance(result, Suspended):
                # Effect needs handling
                effect = result.effect
                
                # Create new continuation from suspended state
                new_k = Continuation(
                    _resume=result.resume,
                    _resume_error=result.resume_error,
                    env=state.E,
                    store=state.S,
                )
                
                # Look up handler
                handler = handlers.lookup(effect)
                if handler is None:
                    # No handler found - resume with error
                    from doeff.cesk import UnhandledEffectError
                    error = UnhandledEffectError(f"No handler for {type(effect).__name__}")
                    state = new_k.resume_error(error, state.S)
                    continue
                
                # Call handler
                handler_result = handler(effect, state.E, state.S, new_k, scheduler)
                
                if isinstance(handler_result, Resume):
                    # Resume immediately
                    state = new_k.resume(handler_result.value, handler_result.store)
                    continue
                
                elif isinstance(handler_result, Suspend):
                    # Await async operation
                    try:
                        value = await handler_result.awaitable
                        state = new_k.resume(value, handler_result.store)
                    except Exception as ex:
                        state = new_k.resume_error(ex, handler_result.store)
                    continue
                
                elif isinstance(handler_result, Scheduled):
                    # Continuation was submitted to scheduler, pick next
                    break
                
                else:
                    raise TypeError(f"Unknown handler result: {type(handler_result)}")
            
            if isinstance(result, CESKState):
                # Normal state transition
                state = result
                continue
            
            raise TypeError(f"Unknown step result: {type(result)}")
    
    if final_error is not None:
        raise final_error
    
    return final_result


def run_with_scheduler_sync(
    program: "Program",
    scheduler: Scheduler,
    handlers: ScheduledHandlerRegistry,
    env: "Environment | dict[Any, Any] | None" = None,
    store: "Store | None" = None,
) -> Any:
    """Synchronous wrapper for run_with_scheduler."""
    return asyncio.run(run_with_scheduler(program, scheduler, handlers, env, store))


# ============================================================================
# Default Handler Adapters
# ============================================================================


def adapt_pure_handler(
    sync_handler: Callable[["EffectBase", "Environment", "Store"], tuple[Any, "Store"]],
) -> ScheduledEffectHandler:
    """Adapt a pure (synchronous) handler to the new protocol.
    
    Pure handlers that return (value, store) are adapted to return Resume.
    The continuation and scheduler are ignored since the handler
    completes synchronously.
    """
    def adapted(
        effect: "EffectBase",
        env: "Environment",
        store: "Store",
        k: Continuation,
        scheduler: Scheduler,
    ) -> HandlerResult:
        value, new_store = sync_handler(effect, env, store)
        return Resume(value, new_store)
    
    return adapted


def adapt_async_handler(
    async_handler: Callable[["EffectBase", "Environment", "Store"], Awaitable[tuple[Any, "Store"]]],
) -> ScheduledEffectHandler:
    """Adapt an async handler to the new protocol.
    
    Async handlers that return awaitable are adapted to return Suspend.
    """
    def adapted(
        effect: "EffectBase",
        env: "Environment",
        store: "Store",
        k: Continuation,
        scheduler: Scheduler,
    ) -> HandlerResult:
        async def do_async():
            value, new_store = await async_handler(effect, env, store)
            return value
        
        return Suspend(do_async(), store)
    
    return adapted


# ============================================================================
# Simulation Effects (examples from spec)
# ============================================================================


@dataclass(frozen=True)
class SimDelay:
    """Simulation delay effect - wait for a duration.
    
    In simulation mode, this advances simulation time.
    In realtime mode, this actually waits.
    """
    seconds: float


@dataclass(frozen=True)
class SimWaitUntil:
    """Simulation wait until time effect.
    
    Wait until a specific simulation time.
    """
    target_time: datetime


@dataclass(frozen=True)
class SimSubmit:
    """Submit a new program to the scheduler.
    
    Creates a new continuation in the same scheduler.
    Useful for concurrent processes in simulation.
    """
    program: "Program"
    daemon: bool = False


def create_sim_delay_handler() -> ScheduledEffectHandler:
    """Create handler for SimDelay effect.
    
    Schedules continuation for later based on delay.
    """
    def handler(
        effect: "EffectBase",
        env: "Environment",
        store: "Store",
        k: Continuation,
        scheduler: Scheduler,
    ) -> HandlerResult:
        assert isinstance(effect, SimDelay)
        if isinstance(scheduler, SimulationScheduler):
            target_time = scheduler.current_time + timedelta(seconds=effect.seconds)
            scheduler.submit(k, hint=target_time)
        elif isinstance(scheduler, RealtimeScheduler):
            scheduler.submit(k, hint=effect.seconds)
        else:
            scheduler.submit(k, hint=timedelta(seconds=effect.seconds))
        
        return Scheduled(store)
    
    return handler


def create_sim_submit_handler() -> ScheduledEffectHandler:
    """Create handler for SimSubmit effect.
    
    Creates new continuation and adds to scheduler.
    """
    def handler(
        effect: "EffectBase",
        env: "Environment",
        store: "Store",
        k: Continuation,
        scheduler: Scheduler,
    ) -> HandlerResult:
        assert isinstance(effect, SimSubmit)
        new_k = Continuation.from_program(effect.program, env, store)
        scheduler.submit(new_k, hint=None)
        return Resume(None, store)
    
    return handler


# ============================================================================
# Exports
# ============================================================================


__all__ = [
    # Handler Results
    "Resume",
    "Suspend",
    "Scheduled",
    "HandlerResult",
    # Continuation
    "Continuation",
    # Scheduler Protocol and Implementations
    "Scheduler",
    "FIFOScheduler",
    "PriorityScheduler",
    "SimulationScheduler",
    "RealtimeScheduler",
    # Handler Protocol
    "ScheduledEffectHandler",
    "ScheduledHandlers",
    "ScheduledHandlerRegistry",
    # Runtime
    "run_with_scheduler",
    "run_with_scheduler_sync",
    # Adapters
    "adapt_pure_handler",
    "adapt_async_handler",
    # Simulation Effects
    "SimDelay",
    "SimWaitUntil",
    "SimSubmit",
    "create_sim_delay_handler",
    "create_sim_submit_handler",
]
