"""``Run(…, name=…)``: the fold spawns a named run's task from a frame of that name.

The scheduler counts a task's steps under the frame that yielded its ``Spawn`` (``StepTally``'s sites). Counterexamples
these checks name: a runtime that spawns every runner from the fold's own frame whatever its name (the named works
count as one site ``fold``); a runtime that reads the name of an unnamed run as something other than the fold; a
``Run`` that takes a name that names no frame.
"""

import pytest
from doeff_core_effects import CloseStepTally, OpenStepTally, StepTally, step_tally_handler
from doeff_stream import Answered, Finish, Next, Run, merge, simulate_fold, subscribe
from stream_test_support import T0, Kick

from doeff import do

KEY = "test-run-name"


@do
def counted(n: int):
    """A run's program that takes steps of its own: it adds ``n`` numbers."""
    total = 0
    for i in range(n):
        total += i
    return total


def tallied(runs: tuple[Run, ...]):
    """A step that opens the step tally, starts ``runs``, closes the tally once all of them ended and finishes with
    the first word of each site (the frame name)."""
    keys = frozenset(run.key for run in runs)

    def step(ended: frozenset, event: object) -> Next[frozenset]:
        match event:
            case Kick():
                return Next(ended, (Run("open", OpenStepTally(KEY), inline=True), *runs))
            case Answered(key="close", value=StepTally() as tally):
                return Next(
                    ended, (Finish(frozenset(s.site.split()[0] for s in tally.sites if s.site)),)
                )
            case Answered(key=key) if key in keys:
                ended = ended | {key}
                if ended == keys:
                    return Next(ended, (Run("close", CloseStepTally(KEY), inline=True),))
                return Next(ended)
        return Next(ended)

    return step


def sites_of(runs: tuple[Run, ...]) -> frozenset:
    return simulate_fold(
        tallied(runs),
        frozenset(),
        merge(subscribe(Kick)),
        events=(Kick(),),
        handlers=(step_tally_handler,),
        start_time=T0,
    )


def test_each_named_run_is_counted_under_its_name() -> None:
    sites = sites_of(
        (
            Run("a", counted(1000), name="query_answered"),
            Run("b", counted(10), name="closed_tags_read"),
        )
    )
    assert {"query_answered", "closed_tags_read"} <= sites, sites
    assert "fold" not in sites, sites


def test_an_unnamed_run_is_counted_under_the_fold() -> None:
    sites = sites_of((Run("a", counted(1000)), Run("b", counted(10))))
    assert "fold" in sites, sites


@pytest.mark.parametrize("name", ["", 3])
def test_a_name_that_names_no_frame_is_refused(name: object) -> None:
    with pytest.raises(TypeError, match="name"):
        Run("a", counted(1), name=name)  # type: ignore[arg-type] — the refusal under test
