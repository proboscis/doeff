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
- One task works every deadline of the handler (cisco-c8 2026-10-03 15:1x): arming many deadlines spawns one
  task; deadlines of the same instant fire in arming order; an earlier deadline armed after a later one fires at
  its own instant (the task waits again when the earliest deadline changes), and disarming the earliest wakes
  the task to wait for the next one — on the virtual clock and on both wall clocks.

The counterexamples — a disarm that does nothing, a re-arm that keeps the earlier arming, a task that keeps
waiting for the deadline it chose before an earlier one was armed, a queue that fires same-instant deadlines
out of arming order — fail the checks below (each check names the timer the broken form would have fired first).
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


def _on_each_clock(program_of: Any) -> dict[str, Any]:
    """Run the program ``program_of()`` builds on the virtual clock and on both wall clocks; answer each run's value."""
    return {
        "sim": run_on_sim(program_of()),
        "async": run(scheduled(await_handler()(async_time_handler()(_on_events(program_of()))))),
        "sync": run(scheduled(sync_time_handler()(_on_events(program_of())))),
    }


@do
def fired_tags(count: int):
    """Wait for ``count`` TimerFired events; answer their tags in the order they came."""
    tags: tuple[Any, ...] = ()
    for _ in range(count):
        event = yield WaitForEvent(TimerFired)
        tags = (*tags, event.tag)
    return tags


@do
def same_instant_two():
    """Arm two deadlines of the same instant (30 ms away) one after the other; answer the order they fire in."""
    now = yield GetTime()
    yield ArmTimer("armed-first", now + timedelta(milliseconds=30))
    yield ArmTimer("armed-second", now + timedelta(milliseconds=30))
    return (yield fired_tags(2))


def test_deadlines_of_the_same_instant_fire_in_arming_order_on_every_clock() -> None:
    # A queue that ordered same-instant deadlines by anything but arming order (the tag, the dict) could fire
    # "armed-second" first.
    assert _on_each_clock(same_instant_two) == {
        "sim": ("armed-first", "armed-second"),
        "async": ("armed-first", "armed-second"),
        "sync": ("armed-first", "armed-second"),
    }


@do
def earlier_armed_after_later():
    """Arm a deadline 80 ms away, then one 30 ms away; answer which fires first and the instants relative to now."""
    now = yield GetTime()
    yield ArmTimer("later", now + timedelta(milliseconds=80))
    yield ArmTimer("earlier", now + timedelta(milliseconds=30))
    first = yield WaitForEvent(TimerFired)
    at = yield GetTime()
    return (first.tag, at - now < timedelta(milliseconds=80))


def test_an_earlier_deadline_armed_after_a_later_one_fires_at_its_own_instant_on_every_clock() -> (
    None
):
    # A task that kept waiting for the deadline it chose first ("later") would fire "earlier" only at 80 ms,
    # after "later" or with it.
    assert _on_each_clock(earlier_armed_after_later) == {
        "sim": ("earlier", True),
        "async": ("earlier", True),
        "sync": ("earlier", True),
    }


def test_disarming_the_earliest_deadline_wakes_the_waiting_task() -> None:
    # The task waits for "reply" (+5 min). Disarming it must wake the task to wait for "give-up" (+10 min): a task
    # left waiting would wake at +5 min for nothing — the virtual clock would move to a disarmed deadline. The wake
    # is the one CompletePromise; the task's one wake promise and its one Spawn are the rest.
    seen: dict[str, int] = {}

    @do
    def program():
        yield ArmTimer("reply", T0 + timedelta(minutes=5))
        yield ArmTimer("give-up", T0 + timedelta(minutes=10))
        yield DisarmTimer("reply")
        return (yield first_timer_and_when())

    fired = run(
        scheduled(
            sim_time_handler(start_time=T0)(
                event_handler()(_counting_scheduler_effects(seen)(timer_handler()(program())))
            )
        )
    )
    assert fired == (TimerFired("give-up"), T0 + timedelta(minutes=10))
    assert seen == {"CompletePromise": 1, "CreatePromise": 2, "Spawn": 1}, seen


def test_many_deadlines_are_worked_by_one_task() -> None:
    # A task per deadline (the shape before cisco-c8 15:1x) would spawn 30 tasks and create 30 promises here.
    seen: dict[str, int] = {}

    @do
    def program():
        for lap in range(30):
            now = yield GetTime()
            yield ArmTimer(("wake", lap), now + timedelta(minutes=1))
            _ = yield WaitForEvent(TimerFired)
        return (yield GetTime())

    ended = run(
        scheduled(
            sim_time_handler(start_time=T0)(
                event_handler()(_counting_scheduler_effects(seen)(timer_handler()(program())))
            )
        )
    )
    assert ended == T0 + timedelta(minutes=30)
    assert seen["Spawn"] == 1, seen
    assert seen.get("CreatePromise", 0) <= 2, seen
