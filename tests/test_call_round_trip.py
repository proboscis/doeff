"""The shape and answer of one round trip of calling a generator ``@do`` definition (``Call``).

When the VM sees a yielded ``Call``, if it is a generator definition it calls the function on the spot and makes it a stream (``Expand(Pure(stream))`` --
the same shape as the ``@do`` handler), and the generator is driven with the C API ``PyIter_Send`` (no StopIteration exception is created).
The answers (values, None, where exceptions are caught) are as before. The VM steps of a round trip went from 5 (``Expand(Apply(Pure(thunk)))``) to 3,
and since #2816, the parent stream drives a yielded ``Call`` on the spot, so a call that returns without emitting an effect uses no VM steps
(tests/test_call_driven_in_place.py). Speed fluctuates with load, so it is not pinned in a test; look at the number of steps.
"""

from collections.abc import Callable, Generator
from dataclasses import dataclass

import pytest
from doeff_vm import PyVM

from doeff import do, run


class BoomError(Exception):
    pass


@do
def leaf(x: int) -> Generator[object, object, int]:
    if False:
        yield
    return x + 1


@do
def returns_none() -> Generator[object, object, None]:
    if False:
        yield


@do
def raises(x: int) -> Generator[object, object, None]:
    if False:
        yield
    raise BoomError(x)


@do
def calls(n: int) -> Generator[object, int, int]:
    total = 0
    for i in range(n):
        total += yield leaf(i)
    return total


@dataclass(frozen=True)
class CountedRun:
    steps: int
    value: object


def _counted_run(program: object) -> CountedRun:
    vm = PyVM()
    value = vm.run(program)
    return CountedRun(steps=vm.step_count(), value=value)


def test_a_call_of_a_generator_definition_that_returns_without_an_effect_takes_no_vm_step() -> None:
    ten = _counted_run(calls(10))
    twenty = _counted_run(calls(20))
    assert (ten.value, twenty.value) == (sum(range(1, 11)), sum(range(1, 21)))
    assert twenty.steps == ten.steps


def test_a_generator_definition_returning_none_answers_none() -> None:
    @do
    def parent() -> Generator[object, object, tuple[str, object]]:
        value = yield returns_none()
        return ("got", value)

    assert run(parent()) == ("got", None)


def test_an_exception_of_the_called_definition_is_caught_where_the_caller_yields() -> None:
    @do
    def parent() -> Generator[object, object, object]:
        try:
            yield raises(1)
        except BoomError as error:
            return ("caught", error.args)
        return "not caught"

    assert run(parent()) == ("caught", (1,))


def _call_with_any_arity(definition: Callable[..., object], *args: object) -> object:
    """Make a ``Call`` regardless of the number of arguments -- a wrong number of arguments is raised when the VM calls the function."""
    return definition(*args)


def test_a_wrong_argument_count_is_a_type_error_the_caller_catches() -> None:
    @do
    def parent() -> Generator[object, object, str]:
        try:
            yield _call_with_any_arity(leaf, 1, 2)
        except TypeError:
            return "caught"
        return "not caught"

    assert run(parent()) == "caught"


def test_an_uncaught_exception_of_the_called_definition_leaves_run() -> None:
    @do
    def parent() -> Generator[object, object, str]:
        yield raises(2)
        return "not raised"

    with pytest.raises(BoomError):
        run(parent())
