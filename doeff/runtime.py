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
    from doeff._vendor import Result
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
class Schedule:
    """Schedule continuation for later resumption via scheduler.
    
    Used when the handler needs to defer execution. The runtime
    passes (k, payload) to the scheduler, which interprets the
    payload and resumes the continuation when ready.
    
    Payload types:
    - Awaitable: await it, resume with result
    - SimDelay: advance simulation time, resume with None
    - Custom payloads: scheduler-specific interpretation
    """
    payload: Any
    store: "Store"


# Deprecated: Use Schedule instead
@dataclass(frozen=True)
class Suspend:
    """[DEPRECATED] Await external async operation, then resume.
    
    Use Schedule(awaitable, store) instead. This class is kept for
    backward compatibility and will be removed in a future version.
    """
    awaitable: Awaitable[Any]
    store: "Store"


# Deprecated: Use Schedule instead
@dataclass(frozen=True)
class Scheduled:
    """[DEPRECATED] Continuation was submitted to scheduler, pick next.
    
    Use Schedule(payload, store) instead. This class is kept for
    backward compatibility and will be removed in a future version.
    """
    store: "Store"


HandlerResult = Resume | Schedule | Suspend | Scheduled


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
    """Pure effect handler: (effect, env, store) -> HandlerResult.
    
    Handlers return one of:
    - Resume(value, store): Immediate - resume with value
    - Schedule(payload, store): Deferred - scheduler handles payload
    
    Handler is pure - no access to continuation or scheduler. The runtime
    takes care of passing (k, payload) to the scheduler when Schedule is returned.
    """
    
    def __call__(
        self,
        effect: "EffectBase",
        env: "Environment",
        store: "Store",
    ) -> HandlerResult:
        ...


class LegacyScheduledEffectHandler(Protocol):
    """[DEPRECATED] Legacy handler with k and scheduler access.
    
    Use ScheduledEffectHandler instead. This signature is kept for
    backward compatibility during migration.
    """
    
    def __call__(
        self,
        effect: "EffectBase",
        env: "Environment",
        store: "Store",
        k: Continuation,
        scheduler: "Scheduler",
    ) -> HandlerResult:
        ...


# Type alias for handler registry
ScheduledHandlers = dict[type["EffectBase"], ScheduledEffectHandler]


class EffectRuntime:
    def __init__(
        self,
        scheduler: "Scheduler | None" = None,
        handlers: ScheduledHandlers | None = None,
    ):
        self._scheduler = scheduler
        self._handlers = handlers
    
    async def run(
        self,
        program: "Program[T]",
        env: "Environment | dict | None" = None,
        store: "Store | None" = None,
    ) -> "RuntimeResult[T]":
        from doeff.cesk import run as cesk_run
        result = await cesk_run(
            program,
            env,
            store,
            scheduled_handlers=self._handlers,
            scheduler=self._scheduler,
        )
        return RuntimeResult(result.result, result.captured_traceback)
    
    def run_sync(
        self,
        program: "Program[T]",
        env: "Environment | dict | None" = None,
        store: "Store | None" = None,
    ) -> "RuntimeResult[T]":
        return asyncio.run(self.run(program, env, store))


@dataclass(frozen=True)
class RuntimeResult(Generic[T]):
    result: "Result[T]"
    captured_traceback: Any = None
    
    @property
    def is_ok(self) -> bool:
        from doeff._vendor import Ok
        return isinstance(self.result, Ok)
    
    @property
    def is_err(self) -> bool:
        from doeff._vendor import Err
        return isinstance(self.result, Err)
    
    @property
    def value(self) -> T:
        return self.result.ok()
    
    @property
    def error(self) -> BaseException:
        return self.result.err()


def create_runtime(
    scheduler: "Scheduler | None" = None,
    handlers: ScheduledHandlers | None = None,
) -> EffectRuntime:
    if scheduler is None:
        scheduler = FIFOScheduler()
    return EffectRuntime(scheduler=scheduler, handlers=handlers)


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
        return len(self._ready)


class AsyncioScheduler:
    def __init__(self) -> None:
        self._pending_tasks: list[asyncio.Task] = []
    
    async def submit(self, k: Continuation, payload: Any) -> None:
        if isinstance(payload, Awaitable):
            result = await payload
            if isinstance(result, tuple) and len(result) == 2:
                value, new_store = result
            else:
                value, new_store = result, k.store
            k.resume(value, new_store)
        elif isinstance(payload, timedelta):
            await asyncio.sleep(payload.total_seconds())
            k.resume(None, k.store)
        elif isinstance(payload, datetime):
            delay = (payload - datetime.now()).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
            k.resume(None, k.store)
        elif isinstance(payload, SimSpawnPayload):
            new_k = Continuation.from_program(payload.program, payload.env, payload.store)
            self._pending_tasks.append(asyncio.create_task(self._run_continuation(new_k)))
            k.resume(None, k.store)
        else:
            raise TypeError(f"AsyncioScheduler cannot handle payload type: {type(payload)}")
    
    async def _run_continuation(self, k: Continuation) -> None:
        pass


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
    def handler(
        effect: "EffectBase",
        env: "Environment",
        store: "Store",
    ) -> HandlerResult:
        assert isinstance(effect, SimDelay)
        return Schedule(timedelta(seconds=effect.seconds), store)
    
    return handler


def create_sim_wait_until_handler() -> ScheduledEffectHandler:
    def handler(
        effect: "EffectBase",
        env: "Environment",
        store: "Store",
    ) -> HandlerResult:
        assert isinstance(effect, SimWaitUntil)
        return Schedule(effect.target_time, store)
    
    return handler


@dataclass(frozen=True)
class SimSpawnPayload:
    program: "Program"
    env: "Environment"
    store: "Store"


def create_sim_submit_handler() -> ScheduledEffectHandler:
    def handler(
        effect: "EffectBase",
        env: "Environment",
        store: "Store",
    ) -> HandlerResult:
        assert isinstance(effect, SimSubmit)
        payload = SimSpawnPayload(program=effect.program, env=env, store=store)
        return Schedule(payload, store)
    
    return handler


# ============================================================================
# Exports
# ============================================================================


__all__ = [
    "Resume",
    "Schedule",
    "Suspend",
    "Scheduled",
    "HandlerResult",
    "Continuation",
    "Scheduler",
    "FIFOScheduler",
    "PriorityScheduler",
    "SimulationScheduler",
    "RealtimeScheduler",
    "AsyncioScheduler",
    "EffectRuntime",
    "RuntimeResult",
    "create_runtime",
    "ScheduledEffectHandler",
    "LegacyScheduledEffectHandler",
    "ScheduledHandlers",
    "SimDelay",
    "SimWaitUntil",
    "SimSubmit",
    "SimSpawnPayload",
    "create_sim_delay_handler",
    "create_sim_wait_until_handler",
    "create_sim_submit_handler",
]
