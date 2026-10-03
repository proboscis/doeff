"""ArmTimer / DisarmTimer / TimerFired — a deadline is an event the program waits for (agora-redesign #3076).

- An armed timer publishes ``TimerFired(tag)`` at its deadline; the program receives it with ``WaitForEvent``.
- A disarmed timer never fires, and re-arming a tag moves its deadline (one fire per arming).
- A ``TimerFired`` carries its tag, so a program tells a stale deadline (an older arming it moved on from)
  from the one its state waits for.
- On the virtual clock a deadline a day away costs the same VM steps as one five minutes away: the clock
  moves to the next deadline in one step while every task waits.
- The same timer handler runs on the wall clocks (async and sync).
- Re-arming a tag with the deadline it already has keeps its waiting task — a worker that re-arms its deadlines
  after every pass pays no new task for the ones that did not move (agora-redesign #3054 C).

The counterexamples — a disarm that does nothing, a re-arm that keeps the earlier arming — fail the checks
below (each check names the timer the broken form would have fired first).
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from doeff_core_effects.handlers import await_handler
from doeff_core_effects.scheduler import CompletePromise, CreatePromise, Spawn, scheduled
from doeff_events import (
    ArmedTimer,
    ArmedTimers,
    ArmTimer,
    DisarmTimer,
    TimerFired,
    WaitForEvent,
    event_handler,
    timer_handler,
)
from doeff_time import GetTime, sim_time_handler
from doeff_time.handlers.async_time import async_time_handler
from doeff_time.handlers.sync_time import sync_time_handler
from doeff_vm.doeff_vm import vm_work_counts

from doeff import Pass, do, run
from doeff import handler as program_handler
from doeff.program import ProgramHandler

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _on_events(program: Any) -> Any:
    """The program under the timer handler, inside the in-memory event handler."""
    return event_handler()(timer_handler()(program))


def run_on_sim(program: Any) -> Any:
    """Run ``program`` on the virtual clock starting at ``T0``."""
    return run(scheduled(sim_time_handler(start_time=T0)(_on_events(program))))


@do
def first_timer_and_when():
    """Wait for the next TimerFired; answer it with the clock's time at that moment."""
    event = yield WaitForEvent(TimerFired)
    now = yield GetTime()
    return (event, now)


def test_an_armed_timer_fires_at_its_deadline() -> None:
    @do
    def program():
        yield ArmTimer("reply", T0 + timedelta(minutes=5))
        return (yield first_timer_and_when())

    assert run_on_sim(program()) == (TimerFired("reply"), T0 + timedelta(minutes=5))


def test_a_disarmed_timer_never_fires() -> None:
    # A disarm that did nothing would fire "reply" at +5 minutes, before "give-up".
    @do
    def program():
        yield ArmTimer("reply", T0 + timedelta(minutes=5))
        yield ArmTimer("give-up", T0 + timedelta(minutes=10))
        yield DisarmTimer("reply")
        armed = yield ArmedTimers()
        fired = yield first_timer_and_when()
        return (armed, fired)

    armed, fired = run_on_sim(program())
    assert armed == (ArmedTimer("give-up", T0 + timedelta(minutes=10)),)
    assert fired == (TimerFired("give-up"), T0 + timedelta(minutes=10))


def test_re_arming_a_tag_moves_its_deadline() -> None:
    # A re-arm that kept the earlier arming would fire "reply" at +5 minutes.
    @do
    def program():
        yield ArmTimer("reply", T0 + timedelta(minutes=5))
        yield ArmTimer("reply", T0 + timedelta(minutes=10))
        armed = yield ArmedTimers()
        fired = yield first_timer_and_when()
        return (armed, fired)

    armed, fired = run_on_sim(program())
    assert armed == (ArmedTimer("reply", T0 + timedelta(minutes=10)),)
    assert fired == (TimerFired("reply"), T0 + timedelta(minutes=10))


def test_a_program_tells_a_stale_deadline_by_its_tag() -> None:
    # The program moved on from attempt 1 to attempt 2 without disarming; attempt 1's deadline still fires
    # and the program sets it aside because its tag is not the one its state waits for.
    @do
    def program():
        yield ArmTimer("attempt-1", T0 + timedelta(minutes=5))
        current = "attempt-2"
        yield ArmTimer(current, T0 + timedelta(minutes=10))
        first = yield WaitForEvent(TimerFired)
        second = yield WaitForEvent(TimerFired)
        stale = tuple(event.tag for event in (first, second) if event.tag != current)
        due = tuple(event.tag for event in (first, second) if event.tag == current)
        return (stale, due)

    assert run_on_sim(program()) == (("attempt-1",), ("attempt-2",))


def test_armed_timers_lists_the_armed_ones_earliest_first() -> None:
    @do
    def program():
        yield ArmTimer("late", T0 + timedelta(hours=2))
        yield ArmTimer("early", T0 + timedelta(hours=1))
        before = yield ArmedTimers()
        _ = yield WaitForEvent(TimerFired)
        after = yield ArmedTimers()
        yield DisarmTimer("late")
        return (before, after, (yield ArmedTimers()))

    before, after, last = run_on_sim(program())
    assert before == (
        ArmedTimer("early", T0 + timedelta(hours=1)),
        ArmedTimer("late", T0 + timedelta(hours=2)),
    )
    assert after == (ArmedTimer("late", T0 + timedelta(hours=2)),)
    assert last == ()


def _steps_to_a_deadline(away: timedelta) -> int:
    """VM steps of a program that arms one timer ``away`` from now and waits for it on the virtual clock."""

    @do
    def program():
        yield ArmTimer("deadline", T0 + away)
        return (yield first_timer_and_when())

    before, _ = vm_work_counts()
    fired = run_on_sim(program())
    after, _ = vm_work_counts()
    assert fired == (TimerFired("deadline"), T0 + away)
    return after - before


def test_a_day_away_deadline_costs_no_more_steps_than_a_minutes_away_one() -> None:
    # A loop that woke each second to check the time would take 86,400 wakes to cross the day.
    minutes = _steps_to_a_deadline(timedelta(minutes=5))
    day = _steps_to_a_deadline(timedelta(hours=24))
    assert day == minutes


def _counting_scheduler_effects(seen: dict[str, int]) -> ProgramHandler:
    """A handler that counts the scheduler effects passing outward (the timer handler's task and promise work)."""

    @do
    def count(effect: Any, k: Any):
        if isinstance(effect, Spawn | CreatePromise | CompletePromise):
            name = type(effect).__name__
            seen[name] = seen.get(name, 0) + 1
        yield Pass(effect, k)

    return program_handler(count)


def test_re_arming_the_same_deadline_keeps_its_waiting_task() -> None:
    # A worker re-arms its deadlines after every pass; the same deadline must not cost a new waiting task.
    # A re-arm that always replaced the arming would spawn 3 tasks and complete 2 disarm promises here.
    seen: dict[str, int] = {}

    @do
    def program():
        for _ in range(3):
            yield ArmTimer("reply", T0 + timedelta(minutes=5))
        armed = yield ArmedTimers()
        fired = yield first_timer_and_when()
        return (armed, fired)

    armed, fired = run(
        scheduled(
            sim_time_handler(start_time=T0)(
                event_handler()(_counting_scheduler_effects(seen)(timer_handler()(program())))
            )
        )
    )
    assert armed == (ArmedTimer("reply", T0 + timedelta(minutes=5)),)
    assert fired == (TimerFired("reply"), T0 + timedelta(minutes=5))
    assert seen == {"CreatePromise": 1, "Spawn": 1}, seen


@do
def keep_one_drop_one():
    """Arm two timers 30 and 80 ms away on the wall clock, disarm the nearer one, wait for the next TimerFired."""
    now = yield GetTime()
    yield ArmTimer("dropped", now + timedelta(milliseconds=30))
    yield ArmTimer("kept", now + timedelta(milliseconds=80))
    yield DisarmTimer("dropped")
    event = yield WaitForEvent(TimerFired)
    return event.tag


def test_the_timer_handler_runs_on_the_async_wall_clock() -> None:
    # await_handler performs scheduler effects and answers the async clock's Await, so it sits between them.
    assert (
        run(scheduled(await_handler()(async_time_handler()(_on_events(keep_one_drop_one())))))
        == "kept"
    )


def test_the_timer_handler_runs_on_the_sync_wall_clock() -> None:
    assert run(scheduled(sync_time_handler()(_on_events(keep_one_drop_one())))) == "kept"
