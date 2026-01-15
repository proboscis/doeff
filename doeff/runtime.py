"""
doeff Runtime Model: Single-shot Algebraic Effects.

This module provides the core runtime abstractions for the doeff effect system.
The scheduler-based execution model has been replaced by concrete runtime
implementations in doeff.runtimes (AsyncioRuntime, SyncRuntime, SimulationRuntime).

Key abstractions:
- Continuation: Single-shot suspended computation
- HandlerResult: Resume | Schedule - how handlers respond to effects
- SchedulePayload: What to execute when scheduling (await, delay, wait_until, spawn)
- ScheduledEffectHandler: Pure handler function signature
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Protocol, TypeVar

from doeff._types_internal import EffectBase

if TYPE_CHECKING:
    from doeff.cesk import CESKState, Environment, Store
    from doeff.program import Program


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
    """Schedule continuation for later resumption via runtime.
    
    Used when the handler needs to defer execution. The runtime
    interprets the payload and resumes the continuation when ready.
    
    Payload types:
    - AwaitPayload: await the awaitable, resume with result
    - DelayPayload: wait for duration, resume with None
    - WaitUntilPayload: wait until target time, resume with None
    - SpawnPayload: spawn child program, resume parent with None
    """
    payload: "SchedulePayload"
    store: "Store"


HandlerResult = Resume | Schedule


# ============================================================================
# Schedule Payload Types
# ============================================================================


@dataclass(frozen=True)
class AwaitPayload:
    """Payload for awaiting an async operation."""
    awaitable: Awaitable[Any]


@dataclass(frozen=True)
class DelayPayload:
    """Payload for delaying execution by a duration."""
    duration: timedelta


@dataclass(frozen=True)
class WaitUntilPayload:
    """Payload for waiting until a specific time."""
    target: datetime


@dataclass(frozen=True)
class SpawnPayload:
    """Payload for spawning a child program."""
    program: "Program"
    env: "Environment"
    store: "Store"


SchedulePayload = AwaitPayload | DelayPayload | WaitUntilPayload | SpawnPayload


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
        from doeff.cesk import CESKState
        
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
# Effect Handler Protocol
# ============================================================================


class ScheduledEffectHandler(Protocol):
    """Pure effect handler: (effect, env, store) -> HandlerResult.
    
    Handlers return one of:
    - Resume(value, store): Immediate - resume with value
    - Schedule(payload, store): Deferred - runtime handles payload
    
    Handler is pure - no access to continuation or scheduler. The runtime
    takes care of executing the payload and resuming the continuation.
    """
    
    def __call__(
        self,
        effect: "EffectBase",
        env: "Environment",
        store: "Store",
    ) -> HandlerResult:
        ...


# Type alias for handler registry
ScheduledHandlers = dict[type["EffectBase"], ScheduledEffectHandler]


# ============================================================================
# Exports
# ============================================================================


__all__ = [
    # Handler results
    "Resume",
    "Schedule",
    "HandlerResult",
    # Payloads
    "AwaitPayload",
    "DelayPayload",
    "WaitUntilPayload",
    "SpawnPayload",
    "SchedulePayload",
    # Continuation
    "Continuation",
    # Handler protocol
    "ScheduledEffectHandler",
    "ScheduledHandlers",
]
