"""The step budget is one contract of both schedulers (#3143).

docs/25-rust-scheduler.md promises that the Python and Rust schedulers handle
the same effects with the same meaning. The step budget (``arm_step_budget``)
is part of that meaning: with a budget armed, a run that keeps waking tasks
without ending fails by name, while it is still running, with
``StepBudgetExceeded`` — under either scheduler, and with the same message.

- The spins below stop on their own after ``SPIN_ROUNDS`` rounds, far past the
  budget, so a scheduler that ignores the budget fails these tests by
  finishing instead of hanging the session.
- With no budget armed (the default, production), neither scheduler reads the
  step count.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from unittest.mock import Mock

import pytest
from doeff_core_effects import (
    CompletePromise,
    CreatePromise,
    SchedulerImplementation,
    Spawn,
    StepBudget,
    StepBudgetExceeded,
    Try,
    Wait,
    scheduled,
    try_handler,
)
from doeff_core_effects import _scheduler as scheduler_module
from doeff_core_effects._scheduler import arm_step_budget, disarm_step_budget

from doeff import Program, do, run

pytestmark = pytest.mark.usefixtures("scheduler_index_self_check")

IMPLEMENTATIONS: list[SchedulerImplementation] = ["python", "rust"]

# Each round takes tens of doeff-vm steps, so the budget is spent within the
# first few hundred rounds; a scheduler that ignores it runs all the rounds.
LIMIT_STEPS = 5_000
SPIN_ROUNDS = 20_000

MESSAGE = re.compile(
    r"step budget exceeded: (\d+) doeff-vm steps since the budget was armed, "
    rf"limit {LIMIT_STEPS} \(while waking (the root body|task \d+)\)"
)


@do
def _signal(promise: object):
    yield CompletePromise(promise, None)


@do
def _signal_and_wait():
    """Signal self: spawn a task completing a fresh promise, wait on the promise, then on the task."""
    promise = yield CreatePromise()
    signaller = yield Spawn(_signal(promise))
    yield Wait(promise.future)
    yield Wait(signaller)


@do
def _spin(rounds: int):
    for _ in range(rounds):
        yield _signal_and_wait()
    return rounds


@do
def _spin_catching(rounds: int):
    """The spin with every exception of the wait caught — the budget must still end the run."""
    for _ in range(rounds):
        try:
            yield _signal_and_wait()
        except Exception:  # the counterexample: a task that swallows everything
            continue
    return rounds


@do
def _spin_under_try(rounds: int):
    """The spin with each wait under ``Try`` — the budget must still end the run."""
    for _ in range(rounds):
        yield Try(_signal_and_wait())
    return rounds


@do
def _spin_in_a_task(rounds: int):
    """The spin in a spawned task the root body waits on."""
    task = yield Spawn(_spin(rounds))
    return (yield Wait(task))


SPINS: list[Callable[[int], Program]] = [_spin, _spin_catching, _spin_under_try, _spin_in_a_task]


def _run(program: Program, implementation: SchedulerImplementation) -> object:
    """Run under the chosen scheduler, with ``Try`` handled outside it (the order doeff-time's tests use)."""
    return run(try_handler(scheduled(program, implementation=implementation)))


@pytest.fixture
def armed() -> Iterator[Callable[[int], StepBudget]]:
    yield arm_step_budget
    disarm_step_budget()


@pytest.mark.parametrize("spin", SPINS, ids=lambda spin: spin.__name__)
@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_a_spin_past_the_budget_fails_by_name(
    implementation: SchedulerImplementation,
    spin: Callable[[int], Program],
    armed: Callable[[int], StepBudget],
) -> None:
    armed(LIMIT_STEPS)
    with pytest.raises(StepBudgetExceeded) as raised:
        _run(spin(SPIN_ROUNDS), implementation)
    error = raised.value
    matched = MESSAGE.fullmatch(str(error))
    assert matched is not None, str(error)
    assert error.limit_steps == LIMIT_STEPS
    assert error.steps_taken == int(matched.group(1))
    assert error.steps_taken > LIMIT_STEPS
    woken = "the root body" if error.task_id is None else f"task {error.task_id}"
    assert matched.group(2) == woken


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_a_spent_budget_fails_the_next_run_with_the_same_error(
    implementation: SchedulerImplementation, armed: Callable[[int], StepBudget]
) -> None:
    """The first wake past the limit names the run's failure; later wakes raise that same error."""
    armed(LIMIT_STEPS)
    with pytest.raises(StepBudgetExceeded) as first:
        _run(_spin(SPIN_ROUNDS), implementation)
    with pytest.raises(StepBudgetExceeded) as second:
        _run(_spin(1), implementation)
    assert second.value is first.value


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_a_short_run_inside_the_budget_finishes(
    implementation: SchedulerImplementation, armed: Callable[[int], StepBudget]
) -> None:
    armed(LIMIT_STEPS)
    assert _run(_spin(5), implementation) == 5


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_a_disarmed_budget_no_longer_fails_runs(
    implementation: SchedulerImplementation, armed: Callable[[int], StepBudget]
) -> None:
    armed(LIMIT_STEPS)
    with pytest.raises(StepBudgetExceeded):
        _run(_spin(SPIN_ROUNDS), implementation)
    disarm_step_budget()
    assert _run(_spin(SPIN_ROUNDS // 10), implementation) == SPIN_ROUNDS // 10


@pytest.fixture
def step_reads(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """The schedulers' reader of the doeff-vm step count, wrapped to count its calls."""
    reader = Mock(wraps=scheduler_module._read_vm_steps)
    monkeypatch.setattr(scheduler_module, "_read_vm_steps", reader)
    return reader


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_no_budget_armed_never_reads_the_step_count(
    implementation: SchedulerImplementation, step_reads: Mock
) -> None:
    disarm_step_budget()
    assert _run(_spin(50), implementation) == 50
    assert step_reads.call_count == 0


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_an_armed_budget_reads_the_step_count_on_each_wake(
    implementation: SchedulerImplementation, step_reads: Mock, armed: Callable[[int], StepBudget]
) -> None:
    armed(LIMIT_STEPS)
    step_reads.reset_mock()
    assert _run(_spin(5), implementation) == 5
    assert step_reads.call_count > 5
