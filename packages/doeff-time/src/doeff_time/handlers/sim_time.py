"""Virtual-clock simulation handler for doeff-time effects."""


from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from doeff_core_effects import WriterTellEffect
from doeff_core_effects.scheduler import (
    PRIORITY_IDLE,
    CompletePromise,
    CreatePromise,
    Future,
    Race,
    Spawn,
    Wait,
)

from doeff import Pass, Transfer, do
from doeff import handler as _program_handler
from doeff.program import ProgramHandler
from doeff_time._internals import SimClock, TimeQueue
from doeff_time.effects import (
    DelayEffect,
    GetMonotonicEffect,
    GetTimeEffect,
    ScheduleAtEffect,
    SetTimeEffect,
    TicksOutcome,
    WaitTicksEffect,
    WaitUntilEffect,
    WaitWithinEffect,
)

ProtocolHandler = Callable[[Any, Any], Any]
LogFormatter = Callable[[datetime, Any], str]
EPOCH_UTC = datetime(1970, 1, 1, tzinfo=timezone.utc)
#: The virtual clock's resolution (datetime keeps microseconds).
CLOCK_TICK = timedelta(microseconds=1)


def _delay_span(seconds: float) -> timedelta:
    """How far a Delay moves the virtual clock: a positive delay moves it by at least one tick.

    ``timedelta(seconds=s)`` rounds to whole microseconds, so a positive delay below half a
    microsecond became zero and the clock did not move. A wait loop that sleeps "the rest of
    its timeout" (e.g. doeff_records.watching.wait-for-changes) then slept the same sub-tick
    remainder forever without advancing virtual time (agora-redesign #675, 2026-09-26: the
    remainder 7.2e-08 s after float drift). A real sleep of a positive duration always lets at
    least that much time pass, so the sim rounds such a delay up to one tick.
    """
    span = timedelta(seconds=seconds)
    if seconds > 0 and span <= timedelta(0):
        return CLOCK_TICK
    return span


class _TickRun:
    """A WaitTicks waiter's ticks on the virtual time queue (agora-redesign #3066): the spacing and the number
    of ticks, how many have passed, and the sequence of the tick queued now (withdrawn when the future wins).
    The clock driver advances it as each tick is taken off the queue; the waiting task reads ``passed``
    once it is answered."""

    def __init__(self, every: timedelta, count: int) -> None:
        self.every = every
        self.count = count
        self._mut_passed = 0
        self._mut_sequence = 0

    @property
    def passed(self) -> int:
        return self._mut_passed

    @property
    def sequence(self) -> int:
        return self._mut_sequence

    def queued(self, sequence: int) -> None:
        """Remember the sequence of the tick just queued."""
        self._mut_sequence = sequence

    def tick(self) -> bool:
        """Count a tick taken off the queue; answers whether it was the last one."""
        self._mut_passed += 1
        return self._mut_passed >= self.count


class SimTimeRuntime:
    """Runtime state for virtual-clock interpretation."""

    def __init__(
        self,
        *,
        clock: SimClock,
        log_formatter: LogFormatter | None,
    ) -> None:
        self._clock = clock
        self._time_queue = TimeQueue()
        # Queued ticks of WaitTicks waiters, by queue sequence: such an entry's promise is the waiter's
        # deadline, completed only at its last tick (agora-redesign #3066).
        self._mut_tick_runs: dict[int, _TickRun] = {}
        self._mut_driver_running = False
        self._log_formatter = log_formatter
        self._mut_forwarding_tell = False
        self._handler: ProtocolHandler = self.handle

    @do
    def _clock_driver(self):
        """Idle-priority daemon that advances time when normal tasks are parked.

        A WaitTicks tick that is not the last is not completed: the next tick is queued the moment
        this one is taken off — where a waiter re-registering WaitWithin at each tick would queue it,
        since no other task runs between the pop and that waiter's re-registration — and the waiting
        task is not woken (agora-redesign #3066)."""

        try:
            while not self._time_queue.empty():
                entry = self._time_queue.pop()
                self._clock.advance_to(entry.time)
                if entry.sequence in self._mut_tick_runs:
                    run = self._mut_tick_runs.pop(entry.sequence)
                    if not run.tick():
                        sequence = self._time_queue.push(entry.time + run.every, entry.promise)
                        run.queued(sequence)
                        self._mut_tick_runs[sequence] = run
                        continue
                yield CompletePromise(entry.promise, None)
        finally:
            self._mut_driver_running = False

    @do
    def _ensure_clock_driver(self):
        if self._mut_driver_running:
            return None
        self._mut_driver_running = True
        # daemon=True carries two contracts:
        # - #501: the driver's final IDLE resume (queued right after its
        #   last CompletePromise) is routinely abandoned when the root body
        #   returns first — that is this daemon's lifecycle, not lost work,
        #   so it must not trip the root close-out diagnostic.
        # - #505: daemon tasks are the only tasks the scheduler's
        #   PRIORITY_EXTERNAL_WAIT shield may starve, which is exactly what
        #   keeps this driver from advancing sim time past a pending
        #   external completion.
        yield Spawn(self._clock_driver(), priority=PRIORITY_IDLE, daemon=True)
        return None

    @do
    def _wait_for_time(self, target_time: datetime):
        promise = yield CreatePromise()
        self._time_queue.push(target_time, promise)
        _ = yield self._ensure_clock_driver()
        yield Wait(promise.future)

    @do
    def _wait_within(self, future: "Future[object]", seconds: float, park: bool):
        """Answer ``future``'s value, or None once ``seconds`` of virtual time pass first.

        The deadline is a promise on this handler's time queue (like a Delay) raced against the
        future — no timer task is spawned, and a deadline the future beat is withdrawn from the
        queue so the clock driver never advances to it (agora-redesign #2618). ``park`` races
        an external promise without shielding the clock driver, so the deadline can pass while
        the promise is pending (agora-redesign #3054).
        """
        deadline = yield CreatePromise()
        sequence = self._time_queue.push(self._clock.current_time + _delay_span(seconds), deadline)
        _ = yield self._ensure_clock_driver()
        try:
            first = yield Race(future, deadline.future, priority=PRIORITY_IDLE if park else None)
        finally:
            self._time_queue.withdraw(sequence)
        return first

    @do
    def _wait_ticks(self, future: "Future[object]", every: float, count: int, park: bool):
        """Answer ``future``'s value, or None once ``count`` ticks spaced ``every`` seconds pass first,
        with how many ticks passed (agora-redesign #3066).

        One tick at a time is on the time queue: the clock driver takes it off and queues the next
        one at once, without waking this task (see _clock_driver), so a tick instant orders against
        the other timers as if this task re-registered WaitWithin at each tick. The last tick
        completes the deadline. A tick the future beat is withdrawn, as WaitWithin's deadline.
        """
        deadline = yield CreatePromise()
        run = _TickRun(_delay_span(every), count)
        sequence = self._time_queue.push(self._clock.current_time + run.every, deadline)
        run.queued(sequence)
        self._mut_tick_runs[sequence] = run
        _ = yield self._ensure_clock_driver()
        try:
            first = yield Race(future, deadline.future, priority=PRIORITY_IDLE if park else None)
        finally:
            self._time_queue.withdraw(run.sequence)
            if run.sequence in self._mut_tick_runs:
                del self._mut_tick_runs[run.sequence]
        return TicksOutcome(value=first, passed=run.passed)

    @do
    def handle(
        self,
        effect: WriterTellEffect
        | DelayEffect
        | WaitUntilEffect
        | GetTimeEffect
        | GetMonotonicEffect
        | ScheduleAtEffect
        | SetTimeEffect
        | WaitWithinEffect
        | WaitTicksEffect,
        k: Any,
    ):
        # The annotation is the clause list below: the VM skips this handler for
        # every other effect without entering Python (doeff_vm._effect_types),
        # the same as the trailing Pass.
        # Every clause performs its final Transfer/Pass from THIS frame.
        # Delegating to a sub-@do that transfers (the pre-2026-07-14 shape)
        # leaves this frame suspended mid-`yield` forever, pinning the
        # Task handle and defeating the scheduler's terminal-entry sweep
        # (ADR-DOE-CORE-EFFECTS-002). Sub-programs that COMPLETE before
        # the Transfer (_wait_for_time, _ensure_clock_driver) are fine.
        if (
            isinstance(effect, WriterTellEffect)
            and self._log_formatter is not None
            and not self._mut_forwarding_tell
        ):
            formatted = self._log_formatter(self._clock.current_time, effect.msg)
            self._mut_forwarding_tell = True
            try:
                result = yield WriterTellEffect(formatted)
            finally:
                self._mut_forwarding_tell = False
            return (yield Transfer(k, result))
        if isinstance(effect, DelayEffect):
            target_time = self._clock.current_time + _delay_span(effect.seconds)
            _ = yield self._wait_for_time(target_time)
            return (yield Transfer(k, None))
        if isinstance(effect, WaitUntilEffect):
            target_time = max(self._clock.current_time, effect.target)
            _ = yield self._wait_for_time(target_time)
            return (yield Transfer(k, None))
        if isinstance(effect, (GetTimeEffect, GetMonotonicEffect)):
            now = self._clock.current_time
            # GetMonotonic reads the virtual clock's POSIX timestamp: virtual
            # time only moves through Delay/WaitUntil/SetTime, and callers use
            # differences between readings.
            reading = now if isinstance(effect, GetTimeEffect) else now.timestamp()
            return (yield Transfer(k, reading))
        if isinstance(effect, (WaitWithinEffect, WaitTicksEffect)):
            answer = yield (
                self._wait_within(effect.future, effect.seconds, effect.park)
                if isinstance(effect, WaitWithinEffect)
                else self._wait_ticks(effect.future, effect.every, effect.count, effect.park)
            )
            return (yield Transfer(k, answer))
        if isinstance(effect, ScheduleAtEffect):

            @do
            def deferred():
                _ = yield self._wait_for_time(effect.time)
                # Wait(task) answers the scheduled program's value (agora-redesign #1159).
                return (yield effect.program)

            task = yield Spawn(deferred())
            return (yield Transfer(k, task))
        if isinstance(effect, SetTimeEffect):
            self._clock.set_time(effect.time)
            if not self._time_queue.empty():
                _ = yield self._ensure_clock_driver()
            return (yield Transfer(k, None))
        yield Pass(effect, k)


def sim_time_handler(
    *,
    start_time: datetime | None = None,
    clock: SimClock | None = None,
    log_formatter: LogFormatter | None = None,
) -> ProgramHandler:
    """Return a virtual-clock handler that delegates core concurrency effects.

    Install it inside ``scheduled`` and outside every ``Spawn`` whose tasks
    sleep on it: spawned tasks inherit the handler, so all of them share one
    virtual clock. Time advances to the earliest pending wake-up only when no
    normal-priority task is runnable; wake-ups at the same instant resume in
    the order they were requested.

    ``clock`` lets the caller own the ``SimClock`` so tests can read
    ``clock.current_time`` synchronously (or move it with ``set_time`` between
    runs). Pass either ``start_time`` or ``clock``, not both.
    """

    if clock is not None and start_time is not None:
        raise ValueError("pass either start_time or clock, not both")
    if clock is None:
        clock = SimClock(EPOCH_UTC if start_time is None else start_time)
    runtime = SimTimeRuntime(clock=clock, log_formatter=log_formatter)
    return _program_handler(runtime._handler)

