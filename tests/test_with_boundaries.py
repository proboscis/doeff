"""WithBoundaries installs a captured boundary stack in one VM step (agora-redesign #3149).

A scheduler starting a task re-enters the handlers and observers captured at
the spawn site (get_inner_boundaries, innermost first). Until #3149 it did so
one WithHandler / WithObserve per layer — each layer one VM step plus an empty
body fiber climbed on return. WithBoundaries installs the same stack in one
step with a single body fiber, so these tests pin down that the two forms are
the same scope:

- the innermost handler answers (order kept — a reversed stack answers
  differently, which is the failure case);
- an observer keeps its position between handlers (it does not see effects a
  handler's own body performs above it);
- effects from a handler body travel to the outer boundaries;
- a Spawn inside the reinstalled stack captures the same boundaries again;
- the one-step form uses fewer VM steps than the nested form;
- a malformed stack is named where it is built.

Boundaries are captured exactly as the scheduler captures them: a catching
handler calls get_inner_boundaries(k) and re-runs a program under them.
"""

import dataclasses
import functools

import pytest
from doeff_core_effects import Spawn, Wait, scheduled
from doeff_vm import Callable as VmCallable
from doeff_vm import WithBoundaries
from doeff_vm import WithHandler as WithHandlerRaw
from doeff_vm import WithObserve as WithObserveRaw
from doeff_vm import doeff_vm as vm_ext

from doeff import EffectBase, Pass, Resume, WithObserve, do, handler, run
from doeff.handler_utils import get_inner_boundaries


class Trace:
    """What the recorders saw, in order — an immutable tuple the recorders rebuild (no list accumulation)."""

    def __init__(self):
        self._mut_entries = ()

    def record(self, entry):
        """Keep one more seen entry (the observer and the sink call this as effects pass)."""
        self._mut_entries = (*self._mut_entries, entry)

    @property
    def entries(self):
        """The entries seen so far, in order."""
        return self._mut_entries


@dataclasses.dataclass(frozen=True)
class Who(EffectBase):
    """Answered by the naming handlers with their own name."""


@dataclasses.dataclass(frozen=True)
class Noise(EffectBase):
    """Performed from a naming handler's own body (not from user code)."""

    label: str


@dataclasses.dataclass(frozen=True)
class Rerun(EffectBase):
    """Ask the capturing handler to run `program` under the captured boundaries."""

    program: object
    one_step: bool


def naming(name):
    """A handler that answers Who with `name`, after performing Noise from its own body."""

    @handler
    @do
    def answer(effect, k):
        if isinstance(effect, Who):
            yield Noise(f"{name}-body")
            return (yield Resume(k, name))
        return (yield Pass(effect, k))

    return answer


def noise_sink(seen):
    """Records every Noise that reaches it, so where handler-body effects travel is visible."""

    @handler
    @do
    def sink(effect, k):
        if isinstance(effect, Noise):
            seen.record(("sink", effect.label))
            return (yield Resume(k, None))
        return (yield Pass(effect, k))

    return sink


def recording_observer(seen, label):
    """An observer that records the effects it sees, tagged with its position label."""

    def observe(effect):
        if isinstance(effect, (Who, Noise)):
            seen.record((label, type(effect).__name__))

    return observe


def reinstall_nested(program, boundaries):
    """The pre-#3149 scheduler form: one WithHandler / WithObserve per layer, innermost first."""
    return functools.reduce(
        lambda inner, entry: (
            WithHandlerRaw(entry[1], inner)
            if entry[0] == "handler"
            else WithObserveRaw(VmCallable(entry[1]), inner)
        ),
        boundaries,
        program,
    )


def reinstall_one_step(program, boundaries):
    """The #3149 form: the whole captured stack in one instruction."""
    entries = [
        (kind, boundary if kind == "handler" else VmCallable(boundary))
        for kind, boundary in boundaries
    ]
    return WithBoundaries(entries, program)


@handler
@do
def capturing(effect, k):
    """Runs Rerun.program under the boundaries between the perform site and here."""
    if isinstance(effect, Rerun):
        boundaries = yield get_inner_boundaries(k)
        reinstall = reinstall_one_step if effect.one_step else reinstall_nested
        result = yield reinstall(effect.program, boundaries)
        return (yield Resume(k, result))
    return (yield Pass(effect, k))


@do
def ask_who():
    return (yield Who())


@do
def rerun(program, one_step):
    return (yield Rerun(program, one_step))


def layered(seen, program):
    """sink( capturing( outer( obs-mid( inner( obs-in( program ))))) ) — innermost first: obs-in, inner, obs-mid, outer."""
    return noise_sink(seen)(
        capturing(
            naming("outer")(
                WithObserve(
                    recording_observer(seen, "obs-mid"),
                    naming("inner")(WithObserve(recording_observer(seen, "obs-in"), program)),
                )
            )
        )
    )


@pytest.mark.parametrize("one_step", [False, True], ids=["nested", "one-step"])
def test_the_innermost_handler_answers_and_observers_keep_their_place(one_step):
    seen = Trace()
    result = run(layered(seen, rerun(ask_who(), one_step)))
    assert result == "inner"
    # Both observers see the Who performed inside them; the inner handler's body
    # Noise is performed above obs-in (not seen there) but below obs-mid (seen
    # there), then reaches the sink. (The trace of the nested form, read on
    # doeff main — the reference both forms must match.)
    assert seen.entries == (
        ("obs-in", "Who"),
        ("obs-mid", "Who"),
        ("obs-mid", "Noise"),
        ("sink", "inner-body"),
    )


def test_the_one_step_form_is_the_same_scope_as_the_nested_form():
    nested_seen, one_seen = Trace(), Trace()
    nested = run(layered(nested_seen, rerun(ask_who(), False)))
    one = run(layered(one_seen, rerun(ask_who(), True)))
    assert one == nested
    assert one_seen.entries == nested_seen.entries


def test_a_reversed_stack_answers_from_the_other_end():
    """Failure case: the stack order is what decides the answer — reversing it changes it."""

    @handler
    @do
    def reversing(effect, k):
        if isinstance(effect, Rerun):
            boundaries = yield get_inner_boundaries(k)
            result = yield reinstall_one_step(effect.program, list(reversed(boundaries)))
            return (yield Resume(k, result))
        return (yield Pass(effect, k))

    seen = Trace()
    program = noise_sink(seen)(
        reversing(naming("outer")(naming("inner")(rerun(ask_who(), True))))
    )
    assert run(program) == "outer"


def test_an_empty_stack_runs_the_body_unchanged():
    assert run(WithBoundaries([], ask_who_or(7))) == 7


class Broken(Exception):
    """Raised by the body, to see an exception leave both forms the same way."""


@do
def ask_then_break():
    name = yield Who()
    raise Broken(name)


@pytest.mark.parametrize("one_step", [False, True], ids=["nested", "one-step"])
def test_an_exception_from_the_body_leaves_through_the_stack(one_step):
    seen = Trace()
    with pytest.raises(Broken, match="^inner$"):
        run(layered(seen, rerun(ask_then_break(), one_step)))
    assert ("sink", "inner-body") in seen.entries


@do
def ask_who_or(value):
    return value


@do
def spawn_who():
    task = yield Spawn(ask_who())
    return (yield Wait(task))


@pytest.mark.parametrize("one_step", [False, True], ids=["nested", "one-step"])
def test_a_spawn_inside_the_reinstalled_stack_sees_the_same_boundaries(one_step):
    seen = Trace()
    result = run(scheduled(layered(seen, rerun(spawn_who(), one_step))))
    assert result == "inner"
    assert ("obs-in", "Who") in seen.entries
    assert ("sink", "inner-body") in seen.entries


def steps_of(program):
    """VM steps spent running `program` (the VM's process-wide counter, before and after)."""
    before, _ = vm_ext.vm_work_counts()
    run(program)
    after, _ = vm_ext.vm_work_counts()
    return after - before


def test_the_one_step_form_spends_fewer_vm_steps_than_the_nested_form():
    """The point of #3149: entering N layers costs one step and no empty body fiber per layer."""
    layers = 8

    def deep(one_step):
        program = functools.reduce(
            lambda inner, i: naming(f"h{i}")(inner), range(layers), rerun(ask_who(), one_step)
        )
        return noise_sink(Trace())(capturing(program))

    nested = steps_of(deep(False))
    one = steps_of(deep(True))
    # At least the per-layer entry step and the empty body fiber's climb for all but one layer.
    assert nested - one >= 2 * (layers - 1), (nested, one)


@pytest.mark.parametrize(
    ("entries", "error", "message"),
    [
        ([("nope", lambda e, k: None)], ValueError, "kind must be 'handler' or 'observer'"),
        ([("handler", 3)], TypeError, "handler must be callable"),
        ([(1, lambda e, k: None)], TypeError, "kind must be a str"),
    ],
)
def test_a_malformed_stack_is_named_where_it_is_built(entries, error, message):
    with pytest.raises(error, match=message):
        WithBoundaries(entries, ask_who())
