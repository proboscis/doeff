"""Simulation time effects for discrete event simulation.

This module provides effects for time-based scheduling in simulations.
SimAwait allows processes to wait until a specific simulation time or
for a relative delay. The scheduler manages multiple processes using
a priority queue ordered by simulation time.

Use cases:
- Trade backtesting: simulate market events at specific timestamps
- Game engines: schedule game events and updates
- Discrete event simulation: model systems with timed events

Example:
    def market_data_replay(events: list[tuple[float, MarketEvent]]):
        for timestamp, event in events:
            yield SimAwait(until=timestamp)  # Wait until specific time
            yield process_event(event)

    def trading_strategy():
        while True:
            signal = yield get_signal()
            yield SimAwait(delay=0.001)  # 1ms reaction delay
            yield execute_trade(signal)

    def game_update_loop():
        frame = 0
        while True:
            yield SimAwait(until=frame * (1/60))  # 60 FPS
            yield update_game_state()
            frame += 1
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from doeff._types_internal import EffectBase

if TYPE_CHECKING:
    from doeff.cesk import Continuation, CESKState, Store, Environment, HandlerResult
    from doeff.types import Effect, Program


@dataclass(frozen=True)
class SimAwaitEffect(EffectBase):
    """Effect to wait until a simulation time or for a delay.

    When this effect is yielded, the current process is suspended
    and scheduled to resume at the target simulation time.

    Two modes:
    - Absolute time (until): resume at a specific simulation time
    - Relative delay (delay): resume after a delay from current time

    If both are specified, 'until' takes precedence.

    Attributes:
        until: The absolute simulation time to resume at (None if using delay)
        delay: The relative delay from current time (used if until is None)
    """
    until: float | None = None
    delay: float | None = None

    def __post_init__(self) -> None:
        if self.until is None and self.delay is None:
            raise ValueError("SimAwait requires either 'until' or 'delay'")
        if self.delay is not None and self.delay < 0:
            raise ValueError(f"SimAwait delay must be non-negative, got {self.delay}")

    def get_target_time(self, current_time: float) -> float:
        """Calculate the target simulation time.

        Args:
            current_time: The current simulation time

        Returns:
            The absolute simulation time to resume at
        """
        if self.until is not None:
            return self.until
        return current_time + (self.delay or 0.0)

    def intercept(
        self, transform: Callable[[Effect], Effect | Program]
    ) -> SimAwaitEffect:
        """Return self - no nested programs to intercept."""
        return self


def SimAwait(
    *,
    until: float | None = None,
    delay: float | None = None,
) -> SimAwaitEffect:
    """Create a SimAwait effect.

    Use 'until' for absolute time (event-based):
        yield SimAwait(until=next_event_time)

    Use 'delay' for relative time (delay-based):
        yield SimAwait(delay=0.1)

    Args:
        until: Absolute simulation time to resume at
        delay: Relative delay from current time

    Returns:
        SimAwaitEffect instance

    Raises:
        ValueError: If neither until nor delay is specified,
                   or if delay is negative
    """
    return SimAwaitEffect(until=until, delay=delay)


@dataclass(frozen=True)
class SimTimeEffect(EffectBase):
    """Effect to get the current simulation time."""

    def intercept(
        self, transform: Callable[[Effect], Effect | Program]
    ) -> SimTimeEffect:
        """Return self - no nested programs to intercept."""
        return self


def SimTime() -> SimTimeEffect:
    """Create an effect to get the current simulation time.

    Returns:
        SimTimeEffect instance
    """
    return SimTimeEffect()


@dataclass
class SchedulerEntry:
    """An entry in the simulation scheduler's priority queue.

    Attributes:
        time: The simulation time when this entry should be executed
        sequence: Tie-breaker for entries with the same time (FIFO order)
        continuation: The continuation to resume
        store: The store state at suspension time
    """
    time: float
    sequence: int
    continuation: Continuation
    store: Store

    def __lt__(self, other: SchedulerEntry) -> bool:
        """Compare by time, then by sequence for FIFO ordering."""
        if self.time != other.time:
            return self.time < other.time
        return self.sequence < other.sequence


@dataclass
class SimulationScheduler:
    """Priority queue-based scheduler for discrete event simulation.

    This scheduler manages multiple processes, each with their own
    continuation, scheduling them based on simulation time.

    The scheduler is stored in the CESK Store under the key "__sim_scheduler__".
    Current simulation time is stored under "__sim_time__".
    """
    queue: list[SchedulerEntry] = field(default_factory=list)
    sequence_counter: int = 0

    def push(self, time: float, continuation: Continuation, store: Store) -> SimulationScheduler:
        """Schedule a continuation to resume at the given time.

        Returns a new scheduler instance (immutable pattern).
        """
        entry = SchedulerEntry(
            time=time,
            sequence=self.sequence_counter,
            continuation=continuation,
            store=store,
        )
        new_queue = self.queue.copy()
        heapq.heappush(new_queue, entry)
        return SimulationScheduler(
            queue=new_queue,
            sequence_counter=self.sequence_counter + 1,
        )

    def pop(self) -> tuple[SchedulerEntry, SimulationScheduler]:
        """Remove and return the earliest scheduled entry.

        Returns a tuple of (entry, new_scheduler).
        Raises IndexError if queue is empty.
        """
        if not self.queue:
            raise IndexError("Scheduler queue is empty")
        new_queue = self.queue.copy()
        entry = heapq.heappop(new_queue)
        return entry, SimulationScheduler(
            queue=new_queue,
            sequence_counter=self.sequence_counter,
        )

    def is_empty(self) -> bool:
        """Check if the scheduler has no pending entries."""
        return len(self.queue) == 0

    def peek_time(self) -> float | None:
        """Get the time of the next scheduled entry, or None if empty."""
        if self.queue:
            return self.queue[0].time
        return None


# Store keys for simulation state
SIM_SCHEDULER_KEY = "__sim_scheduler__"
SIM_TIME_KEY = "__sim_time__"


def handle_sim_await(
    effect: SimAwaitEffect,
    env: Environment,
    store: Store,
    k: Continuation,
) -> HandlerResult:
    """Handler for SimAwaitEffect using the new handler protocol.

    This handler:
    1. Gets the current simulation time and scheduler from store
    2. Calculates target time (absolute 'until' or relative 'delay')
    3. Schedules the current continuation to resume at target time
    4. Pops the next scheduled entry and transitions to that state

    Args:
        effect: The SimAwaitEffect with until or delay
        env: Current environment
        store: Current store (must contain scheduler and time)
        k: Current continuation

    Returns:
        Next: transition to the next scheduled process's state
    """
    from doeff.cesk import Next, Resume

    # Get scheduler and current time from store
    scheduler: SimulationScheduler = store.get(SIM_SCHEDULER_KEY)
    if scheduler is None:
        raise RuntimeError(
            "SimAwait used outside of simulation context. "
            "Use run_simulation() or initialize scheduler in store."
        )

    current_time: float = store.get(SIM_TIME_KEY, 0.0)

    # Calculate target time (supports both 'until' and 'delay')
    target_time = effect.get_target_time(current_time)

    # Schedule current continuation to resume at target time
    scheduler = scheduler.push(target_time, k, store)

    # Pop the next entry (might be the one we just pushed, or an earlier one)
    entry, scheduler = scheduler.pop()

    # Update store with new scheduler state and time
    new_store = {
        **entry.store,
        SIM_SCHEDULER_KEY: scheduler,
        SIM_TIME_KEY: entry.time,
    }

    # Resume the next process's continuation
    next_state = entry.continuation.resume(None, new_store)
    return Next(state=next_state)


def handle_sim_time(
    effect: SimTimeEffect,
    env: Environment,
    store: Store,
    k: Continuation,
) -> HandlerResult:
    """Handler for SimTimeEffect - returns current simulation time.

    Args:
        effect: The SimTimeEffect
        env: Current environment
        store: Current store
        k: Current continuation

    Returns:
        Resume: immediately resume with current simulation time
    """
    from doeff.cesk import Resume

    current_time = store.get(SIM_TIME_KEY, 0.0)
    return Resume(value=current_time, store=store)


__all__ = [
    "SimAwait",
    "SimAwaitEffect",
    "SimTime",
    "SimTimeEffect",
    "SimulationScheduler",
    "handle_sim_await",
    "handle_sim_time",
    "SIM_SCHEDULER_KEY",
    "SIM_TIME_KEY",
]
