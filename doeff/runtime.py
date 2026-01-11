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

from doeff._types_internal import EffectBase

if TYPE_CHECKING:
    from typing import Callable
    from doeff.cesk import CESKState, Environment, Store
    from doeff.program import Program
    from doeff.types import Effect


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
# Simulation Effects (examples from spec)
# ============================================================================


@dataclass(frozen=True, kw_only=True)
class SimDelay(EffectBase):
    """Simulation delay effect - wait for a duration.
    
    In simulation mode, this advances simulation time.
    In realtime mode, this actually waits.
    """
    seconds: float

    def intercept(
        self, transform: "Callable[[Effect], Effect | Program]"
    ) -> "SimDelay":
        return self


@dataclass(frozen=True, kw_only=True)
class SimWaitUntil(EffectBase):
    """Simulation wait until time effect.
    
    Wait until a specific simulation time.
    """
    target_time: datetime

    def intercept(
        self, transform: "Callable[[Effect], Effect | Program]"
    ) -> "SimWaitUntil":
        return self


@dataclass(frozen=True, kw_only=True)
class SimSubmit(EffectBase):
    """Submit a new program to the scheduler.
    
    Creates a new continuation in the same scheduler.
    Useful for concurrent processes in simulation.
    
    Note: The `daemon` field is reserved for future use.
    """
    program: "Program"
    daemon: bool = False

    def intercept(
        self, transform: "Callable[[Effect], Effect | Program]"
    ) -> "SimSubmit":
        from doeff.program import Program
        new_program = self.program.intercept(transform)
        if new_program is self.program:
            return self
        return SimSubmit(program=new_program, daemon=self.daemon, created_at=self.created_at)


def create_sim_delay_handler() -> ScheduledEffectHandler:
    """Create handler for SimDelay effect.
    
    Schedules continuation for later based on delay.
    For SimulationScheduler: uses scheduler's time-based scheduling.
    For RealtimeScheduler: uses Suspend with asyncio.sleep for wall-clock delay.
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
            return Scheduled(store)
        elif isinstance(scheduler, RealtimeScheduler):
            async def wait_and_resume():
                await asyncio.sleep(effect.seconds)
                return None
            return Suspend(wait_and_resume(), store)
        else:
            scheduler.submit(k, hint=timedelta(seconds=effect.seconds))
            return Scheduled(store)
    
    return handler


def create_sim_wait_until_handler() -> ScheduledEffectHandler:
    """Create handler for SimWaitUntil effect.
    
    Schedules continuation until target time is reached.
    For SimulationScheduler: uses scheduler's time-based scheduling.
    For RealtimeScheduler: uses Suspend with asyncio.sleep for wall-clock delay.
    If target time is in the past, resumes immediately.
    """
    def handler(
        effect: "EffectBase",
        env: "Environment",
        store: "Store",
        k: Continuation,
        scheduler: Scheduler,
    ) -> HandlerResult:
        assert isinstance(effect, SimWaitUntil)
        if isinstance(scheduler, SimulationScheduler):
            if effect.target_time <= scheduler.current_time:
                scheduler.submit(k, hint=None)
            else:
                scheduler.submit(k, hint=effect.target_time)
            return Scheduled(store)
        elif isinstance(scheduler, RealtimeScheduler):
            now = datetime.now()
            delay = (effect.target_time - now).total_seconds()
            if delay <= 0:
                return Resume(None, store)
            async def wait_and_resume():
                await asyncio.sleep(delay)
                return None
            return Suspend(wait_and_resume(), store)
        else:
            scheduler.submit(k, hint=effect.target_time)
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
    "Resume",
    "Suspend",
    "Scheduled",
    "HandlerResult",
    "Continuation",
    "Scheduler",
    "FIFOScheduler",
    "PriorityScheduler",
    "SimulationScheduler",
    "RealtimeScheduler",
    "ScheduledEffectHandler",
    "ScheduledHandlers",
    "SimDelay",
    "SimWaitUntil",
    "SimSubmit",
    "create_sim_delay_handler",
    "create_sim_wait_until_handler",
    "create_sim_submit_handler",
]
