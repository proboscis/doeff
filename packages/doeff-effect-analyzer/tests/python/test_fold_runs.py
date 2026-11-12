"""The Programs a doeff-stream fold runs from the ``Run`` values its step builds, and a carrier handed a
value made at run time.

Regression target: ``fold`` spawns ``_reported(…, program)`` for each ``Run(key, program)`` the step answers,
``program`` being a value taken out of the step's answer at run time. The step itself is never performed — the fold
only calls it — so the reader did not see the Runs, and what their programs perform (and what the handlers answering
them perform) was missing from every closure check of a Program that folds a stream. ``fold`` declares ``__doeff_runs_built__ =
{"step": Spawn}`` and ``Run`` declares ``__doeff_runs_carried__ = {"program"}``: the reader reads each Run's program
where the step (or a function it calls) builds it, carried by ``Spawn``.

A carrier handed a Program the reader cannot follow (``Spawn(program)`` with ``program`` taken out of data) names it
as a place it cannot follow instead of dropping it.
"""

import pytest
from doeff_effect_analyzer._effects import FoldCarried
from doeff_effect_analyzer._program_effects import analyze_program

pytest.importorskip("doeff_stream")

EFFECTS_PY = """\
from doeff import EffectBase


class Work(EffectBase):
    pass


class Tick(EffectBase):
    pass


class Helped(EffectBase):
    pass


class Planned(EffectBase):
    pass


class Inline(EffectBase):
    pass
"""

PROGRAMS_PY = """\
from doeff import do
from doeff_core_effects import Spawn
from doeff_stream import Answered, Finish, Next, Run, fold

from {pkg}.effects import Helped, Inline, Planned, Tick, Work


@do
def work():
    return (yield Work())


@do
def helped():
    return (yield Helped())


def step(state, event):
    match event:
        case Answered():
            return Next(state, (Finish(state),))
        case _:
            return Next(state + 1, (Run("work", work()), Run("tick", Tick()), Run("inline", Inline(), inline=True)))


def _planned_runs(state):
    return (Run(("planned", state), Planned()),)


@do
def _helped_next(state):
    return Next(state, (Run("helped", helped()),))


@do
def step_with_helpers(state, event):
    answer = yield _helped_next(state)
    return Next(answer.state, answer.effects + _planned_runs(state))


@do
def folds(inputs):
    return (yield fold(step, 0, inputs))


@do
def folds_through_helpers(inputs):
    return (yield fold(step_with_helpers, 0, inputs))


@do
def folds_a_step_it_is_handed(step_given, inputs):
    return (yield fold(step_given, 0, inputs))


@do
def spawns_what_it_is_handed(programs):
    task = yield Spawn(programs[0])
    return task


@do
def spawns_an_effect():
    task = yield Spawn(Tick())
    return task


@do
def runs_a_run_without_declaring(runs):
    for run in runs:
        match run:
            case Run(program=program):
                yield program
"""

SPAWN = FoldCarried(names=("Spawn",))


@pytest.fixture
def pkg(make_package) -> str:
    return make_package({"effects.py": EFFECTS_PY, "programs.py": PROGRAMS_PY})


def _short(names: list[str]) -> set[str]:
    return {name.rsplit(".", 1)[-1] for name in names}


def test_the_programs_of_the_runs_a_step_builds_are_spawned_where_the_fold_is_performed(
    pkg: str,
) -> None:
    left = analyze_program(f"{pkg}.programs:folds").residual_with(SPAWN)

    # An inline Run is read the same: the fold takes its program out in a case of its own.
    assert {"Work", "Tick", "Inline"} <= _short(left.effect_names)
    assert left.unresolved == ()


def test_the_runs_are_carried_by_spawn_and_not_performed_by_the_fold_itself(pkg: str) -> None:
    # The counter-case: with Spawn's carried Programs not folded in, the runs' effects do not leave.
    left = analyze_program(f"{pkg}.programs:folds").residual

    assert not {"Work", "Tick", "Inline"} & _short(left.effect_names)


def test_runs_built_in_the_functions_the_step_calls_are_read_too(pkg: str) -> None:
    left = analyze_program(f"{pkg}.programs:folds_through_helpers").residual_with(SPAWN)

    assert {"Helped", "Planned"} <= _short(left.effect_names)
    assert left.unresolved == ()


def test_a_step_the_reader_cannot_read_is_named_as_a_place_it_cannot_follow(pkg: str) -> None:
    left = analyze_program(f"{pkg}.programs:folds_a_step_it_is_handed").residual_with(SPAWN)

    assert [(u.reason, u.text) for u in left.unresolved] == [
        ("handed a function whose built Programs cannot be read", "step_given")
    ]


def test_a_spawn_of_a_program_made_at_run_time_is_named_not_dropped(pkg: str) -> None:
    # The failing case before the fix: Spawn(programs[0]) carried nothing the reader could see, so a closure check
    # folding Spawn in saw no effect and no unfollowed place — a hole that looked closed.
    left = analyze_program(f"{pkg}.programs:spawns_what_it_is_handed").residual_with(SPAWN)

    assert [u.text for u in left.unresolved] == ["programs[0]"]


def test_a_spawn_of_an_effect_runs_that_effect(pkg: str) -> None:
    left = analyze_program(f"{pkg}.programs:spawns_an_effect").residual_with(SPAWN)

    assert "Tick" in _short(left.effect_names)
    assert left.unresolved == ()


def test_a_run_taken_apart_outside_a_declaring_function_is_still_unfollowed(pkg: str) -> None:
    # The counter-case: only a function declaring __doeff_runs_built__ has the Runs it runs read where they were
    # built; another function running a Run's program performs a value the reader cannot follow.
    left = analyze_program(f"{pkg}.programs:runs_a_run_without_declaring").residual

    assert [u.text for u in left.unresolved] == ["program"]
