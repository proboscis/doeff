"""A sim run that keeps waking tasks at one virtual instant fails by name while it runs (#3143).

The scenario: a task signals itself at the same virtual instant forever — it creates a promise, spawns a
task that completes it, and waits on it — so the scheduler keeps waking tasks while the virtual clock never
advances. A wall-clock timeout cannot interrupt the VM's step loop, so without a step budget the run never ends.

- With a step budget armed, the scheduler raises ``StepBudgetExceeded`` (naming the task it was about to wake,
  the steps taken since arming and the limit) — even when the spinning task catches every exception or wraps
  its wait in ``Try``. ``Try`` reinstalls the scheduler's handlers around its program and catches ``Exception``,
  so an ``Exception`` raised by the scheduler would come back to the spinning task as ``Err`` and the spin would
  go on; ``StepBudgetExceeded`` is therefore a ``BaseException`` (like ``KeyboardInterrupt``) and ends the run.
- With no budget armed (the default, production), the scheduler never reads the step count.
"""

import time
from collections.abc import Callable, Iterator
from unittest.mock import Mock

import pytest
from doeff_core_effects import Try
from doeff_core_effects import scheduler as scheduler_module
from doeff_core_effects.scheduler import (
    CompletePromise,
    CreatePromise,
    Spawn,
    StepBudget,
    StepBudgetExceeded,
    Wait,
    arm_step_budget,
    disarm_step_budget,
)
from doeff_time import GetTime, sim_time_handler
from time_test_support import run_with_handlers, sim_time

from doeff import Program, do

# Well below what the spin reaches in a few seconds, well above a short bounded run.
LIMIT_STEPS = 200_000
# The run must fail well inside the per-test limit of 60 seconds.
DEADLINE_SECONDS = 60.0


@do
def _signal(promise: object):
    yield CompletePromise(promise, None)


@do
def _signal_and_wait():
    """Signal self at the current instant: spawn a task completing a fresh promise, wait on the promise,
    then on the signalling task (so a bounded run leaves no work behind)."""
    promise = yield CreatePromise()
    signaller = yield Spawn(_signal(promise))
    yield Wait(promise.future)
    yield Wait(signaller)


@do
def _spin_forever():
    while True:
        yield _signal_and_wait()


@do
def _spin_forever_catching():
    """The spin with every exception of the wait caught — the budget must still end the run."""
    while True:
        try:
            yield _signal_and_wait()
        except Exception:  # noqa: BLE001 - the counterexample: a task that swallows everything
            continue


@do
def _spin_forever_under_try():
    """The spin with each wait under ``Try`` — the budget must still end the run."""
    while True:
        yield Try(_signal_and_wait())


@do
def _spin(count: int):
    """The same spin, bounded: ``count`` self-signals at one instant, then the virtual time."""
    for _ in range(count):
        yield _signal_and_wait()
    return (yield GetTime())


def _run_on_sim_clock(program: Program) -> object:
    return run_with_handlers(sim_time_handler(start_time=sim_time(0.0))(program))


@pytest.fixture
def armed() -> Iterator[Callable[[int], StepBudget]]:
    yield arm_step_budget
    disarm_step_budget()


@pytest.mark.parametrize("program", [_spin_forever, _spin_forever_catching, _spin_forever_under_try])
def test_a_spin_at_one_instant_fails_by_name_with_a_budget_armed(
    program: Callable[[], Program], armed: Callable[[int], StepBudget]
) -> None:
    armed(LIMIT_STEPS)
    started = time.monotonic()
    with pytest.raises(StepBudgetExceeded) as raised:
        _run_on_sim_clock(program())
    assert time.monotonic() - started < DEADLINE_SECONDS
    error = raised.value
    assert error.limit_steps == LIMIT_STEPS
    assert error.steps_taken > LIMIT_STEPS
    # The task about to wake: a spawned task's id, or None for the root body.
    woken = "the root body" if error.task_id is None else f"task {error.task_id}"
    assert f"limit {LIMIT_STEPS} (while waking {woken})" in str(error)


def test_the_bounded_spin_stays_at_one_instant() -> None:
    """The scenario does not move the virtual clock: the busy loop is at one instant."""
    assert _run_on_sim_clock(_spin(50)) == sim_time(0.0)


@pytest.fixture
def step_reads(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """The scheduler's reader of the doeff-vm step count, wrapped to count its calls."""
    reader = Mock(wraps=scheduler_module._read_vm_steps)
    monkeypatch.setattr(scheduler_module, "_read_vm_steps", reader)
    return reader


def test_no_budget_armed_never_reads_the_step_count(step_reads: Mock) -> None:
    disarm_step_budget()

    assert _run_on_sim_clock(_spin(50)) == sim_time(0.0)
    assert step_reads.call_count == 0


def test_an_armed_budget_reads_the_step_count_and_lets_a_short_run_finish(
    step_reads: Mock, armed: Callable[[int], StepBudget]
) -> None:
    armed(LIMIT_STEPS)

    assert _run_on_sim_clock(_spin(50)) == sim_time(0.0)
    assert step_reads.call_count > 1


def test_a_non_positive_budget_is_refused() -> None:
    with pytest.raises(ValueError, match="positive"):
        arm_step_budget(0)
