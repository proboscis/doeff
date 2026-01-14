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
    - AwaitPayload: await the awaitable, resume with result
    - DelayPayload: wait for duration, resume with None
    - WaitUntilPayload: wait until target time, resume with None
    - SpawnPayload: spawn child program, resume parent with None
    """
    payload: "SchedulePayload"
    store: "Store"


# Deprecated: Use Schedule instead
@dataclass(frozen=True)
class Suspend:
    """[DEPRECATED] Await external async operation, then resume.
    
    Use Schedule(AwaitPayload(awaitable), store) instead. This class is kept for
    backward compatibility and will be removed in a future version.
    """
    awaitable: Awaitable[Any]
    store: "Store"
    
    def __post_init__(self) -> None:
        import warnings
        warnings.warn(
            "Suspend is deprecated. Use Schedule(AwaitPayload(awaitable), store) instead.",
            DeprecationWarning,
            stacklevel=3,
        )

    def __post_init__(self) -> None:
        import warnings
        warnings.warn(
            "Suspend is deprecated. Use Schedule(AwaitPayload(awaitable), store) instead.",
            DeprecationWarning,
            stacklevel=2,
        )


# Deprecated: Use Schedule instead
@dataclass(frozen=True)
class Scheduled:
    """[DEPRECATED] Continuation was submitted to scheduler, pick next.
    
    Use Schedule(payload, store) instead. This class is kept for
    backward compatibility and will be removed in a future version.
    """
    store: "Store"
    
    def __post_init__(self) -> None:
        import warnings
        warnings.warn(
            "Scheduled is deprecated. Use Schedule(payload, store) instead.",
            DeprecationWarning,
            stacklevel=3,
        )

    def __post_init__(self) -> None:
        import warnings
        warnings.warn(
            "Scheduled is deprecated. Use Schedule(payload, store) instead.",
            DeprecationWarning,
            stacklevel=2,
        )


HandlerResult = Resume | Schedule | Suspend | Scheduled


@dataclass(frozen=True)
class AwaitPayload:
    awaitable: Awaitable[Any]


@dataclass(frozen=True)
class DelayPayload:
    duration: timedelta


@dataclass(frozen=True)
class WaitUntilPayload:
    target: datetime


@dataclass(frozen=True)
class SpawnPayload:
    program: "Program"
    env: "Environment"
    store: "Store"


SchedulePayload = AwaitPayload | DelayPayload | WaitUntilPayload | SpawnPayload


@dataclass(frozen=True)
class Ready:
    value: Any


@dataclass(frozen=True)
class Pending:
    awaitable: Awaitable[tuple[Any, "Store"]]


SchedulerResult = Ready | Pending


@dataclass
class SchedulerItem:
    k: "Continuation"
    result: SchedulerResult
    store: "Store"


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


class Scheduler(Protocol):
    def submit(self, k: Continuation, payload: SchedulePayload, store: "Store") -> None: ...
    def next(self) -> SchedulerItem | None: ...


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


async def _sleep_with_store(seconds: float, store: "Store") -> tuple[None, "Store"]:
    await asyncio.sleep(seconds)
    return (None, store)


def _compute_delay_seconds(target: datetime) -> float:
    now = datetime.now(tz=target.tzinfo)
    return max(0.0, (target - now).total_seconds())


class FIFOScheduler:
    def __init__(self) -> None:
        self._queue: deque[SchedulerItem] = deque()
    
    def submit(self, k: Continuation, payload: SchedulePayload, store: "Store") -> None:
        match payload:
            case AwaitPayload(awaitable):
                self._queue.append(SchedulerItem(k, Pending(awaitable), store))
            case DelayPayload(duration):
                self._queue.append(SchedulerItem(k, Pending(_sleep_with_store(duration.total_seconds(), store)), store))
            case WaitUntilPayload(target):
                delay = _compute_delay_seconds(target)
                self._queue.append(SchedulerItem(k, Pending(_sleep_with_store(delay, store)), store))
            case SpawnPayload(program, env, st):
                new_k = Continuation.from_program(program, env, st)
                self._queue.append(SchedulerItem(k, Ready(None), store))
                self._queue.append(SchedulerItem(new_k, Ready(None), st))
            case _:
                raise TypeError(f"Unknown payload type: {type(payload)}")
    
    def next(self) -> SchedulerItem | None:
        return self._queue.popleft() if self._queue else None
    
    def __len__(self) -> int:
        return len(self._queue)


class PriorityScheduler:
    def __init__(self) -> None:
        self._queue: list[tuple[int, int, SchedulerItem]] = []
        self._seq: int = 0
    
    def submit(self, k: Continuation, payload: SchedulePayload, store: "Store") -> None:
        match payload:
            case AwaitPayload(awaitable):
                item = SchedulerItem(k, Pending(awaitable), store)
            case DelayPayload(duration):
                item = SchedulerItem(k, Pending(_sleep_with_store(duration.total_seconds(), store)), store)
            case WaitUntilPayload(target):
                delay = _compute_delay_seconds(target)
                item = SchedulerItem(k, Pending(_sleep_with_store(delay, store)), store)
            case SpawnPayload(program, env, st):
                new_k = Continuation.from_program(program, env, st)
                heapq.heappush(self._queue, (0, self._seq, SchedulerItem(new_k, Ready(None), st)))
                self._seq += 1
                item = SchedulerItem(k, Ready(None), store)
            case _:
                raise TypeError(f"Unknown payload type: {type(payload)}")
        heapq.heappush(self._queue, (0, self._seq, item))
        self._seq += 1
    
    def next(self) -> SchedulerItem | None:
        if self._queue:
            _, _, item = heapq.heappop(self._queue)
            return item
        return None
    
    def __len__(self) -> int:
        return len(self._queue)


class SimulationScheduler:
    def __init__(self, start_time: datetime | None = None) -> None:
        self._ready: list[tuple[Continuation, "Store"]] = []
        self._ready_async: list[tuple[Continuation, Awaitable[Any], "Store"]] = []
        self._timed: list[tuple[datetime, int, Continuation, "Store"]] = []
        self._current_time: datetime = start_time or datetime.now()
        self._seq: int = 0
    
    @property
    def current_time(self) -> datetime:
        return self._current_time
    
    def submit(self, k: Continuation, payload: SchedulePayload, store: "Store") -> None:
        match payload:
            case AwaitPayload(awaitable):
                self._ready_async.append((k, awaitable, store))
            case DelayPayload(duration):
                target_time = self._current_time + duration
                heapq.heappush(self._timed, (target_time, self._seq, k, store))
                self._seq += 1
            case WaitUntilPayload(target):
                heapq.heappush(self._timed, (target, self._seq, k, store))
                self._seq += 1
            case SpawnPayload(program, env, st):
                new_k = Continuation.from_program(program, env, st)
                self._ready.append((new_k, st))
                self._ready.append((k, store))
            case _:
                raise TypeError(f"Unknown payload type: {type(payload)}")
    
    def next(self) -> SchedulerItem | None:
        if self._ready_async:
            k, awaitable, store = self._ready_async.pop()
            return SchedulerItem(k, Pending(awaitable), store)
        if self._ready:
            k, store = self._ready.pop()
            return SchedulerItem(k, Ready(None), store)
        if self._timed:
            time, _, k, store = heapq.heappop(self._timed)
            self._current_time = time
            return SchedulerItem(k, Ready(None), store)
        return None
    
    def advance_time(self, delta: timedelta) -> None:
        self._current_time += delta
    
    def set_time(self, time: datetime) -> None:
        self._current_time = time
    
    def __len__(self) -> int:
        return len(self._ready) + len(self._ready_async) + len(self._timed)


class RealtimeScheduler:
    def __init__(self) -> None:
        self._queue: deque[SchedulerItem] = deque()
    
    def submit(self, k: Continuation, payload: SchedulePayload, store: "Store") -> None:
        match payload:
            case AwaitPayload(awaitable):
                self._queue.append(SchedulerItem(k, Pending(awaitable), store))
            case DelayPayload(duration):
                self._queue.append(SchedulerItem(k, Pending(_sleep_with_store(duration.total_seconds(), store)), store))
            case WaitUntilPayload(target):
                delay = _compute_delay_seconds(target)
                self._queue.append(SchedulerItem(k, Pending(_sleep_with_store(delay, store)), store))
            case SpawnPayload(program, env, st):
                new_k = Continuation.from_program(program, env, st)
                self._queue.append(SchedulerItem(k, Ready(None), store))
                self._queue.append(SchedulerItem(new_k, Ready(None), st))
            case _:
                raise TypeError(f"Unknown payload type: {type(payload)}")
    
    def next(self) -> SchedulerItem | None:
        return self._queue.popleft() if self._queue else None
    
    def __len__(self) -> int:
        return len(self._queue)


class AsyncioScheduler:
    def __init__(self) -> None:
        self._queue: deque[SchedulerItem] = deque()
    
    def submit(self, k: Continuation, payload: SchedulePayload, store: "Store") -> None:
        match payload:
            case AwaitPayload(awaitable):
                self._queue.append(SchedulerItem(k, Pending(awaitable), store))
            case DelayPayload(duration):
                self._queue.append(SchedulerItem(k, Pending(_sleep_with_store(duration.total_seconds(), store)), store))
            case WaitUntilPayload(target):
                delay = _compute_delay_seconds(target)
                self._queue.append(SchedulerItem(k, Pending(_sleep_with_store(delay, store)), store))
            case SpawnPayload(program, env, st):
                new_k = Continuation.from_program(program, env, st)
                self._queue.append(SchedulerItem(k, Ready(None), store))
                self._queue.append(SchedulerItem(new_k, Ready(None), st))
            case _:
                raise TypeError(f"Unknown payload type: {type(payload)}")
    
    def next(self) -> SchedulerItem | None:
        return self._queue.popleft() if self._queue else None


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
        return Schedule(DelayPayload(timedelta(seconds=effect.seconds)), store)
    
    return handler


def create_sim_wait_until_handler() -> ScheduledEffectHandler:
    def handler(
        effect: "EffectBase",
        env: "Environment",
        store: "Store",
    ) -> HandlerResult:
        assert isinstance(effect, SimWaitUntil)
        return Schedule(WaitUntilPayload(effect.target_time), store)
    
    return handler


def create_sim_submit_handler() -> ScheduledEffectHandler:
    def handler(
        effect: "EffectBase",
        env: "Environment",
        store: "Store",
    ) -> HandlerResult:
        assert isinstance(effect, SimSubmit)
        return Schedule(SpawnPayload(program=effect.program, env=env, store=store), store)
    
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
    "AwaitPayload",
    "DelayPayload",
    "WaitUntilPayload",
    "SpawnPayload",
    "SchedulePayload",
    "Ready",
    "Pending",
    "SchedulerResult",
    "SchedulerItem",
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
    "create_sim_delay_handler",
    "create_sim_wait_until_handler",
    "create_sim_submit_handler",
]
