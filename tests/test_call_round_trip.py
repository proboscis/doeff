"""生成器の ``@do`` の定義を呼ぶ 1 回(``Call``)の往復の形と答え(agora-redesign #2801)。

VM は yield された ``Call`` を、生成器の定義ならその場で関数を呼んで stream にし(``Expand(Pure(stream))`` —
``@do`` の handler と同じ形)、生成器は C API の ``PyIter_Send`` で回す(StopIteration の例外を作らない)。
答え(値・None・例外の捕まる所)は前と同じ。往復の VM の歩は 5 つ(``Expand(Apply(Pure(thunk)))``)から 3 つになり、
#2816 からは、親の stream が yield された ``Call`` をその場で回すので、effect を出さずに返る呼びは VM の歩を使わない
(tests/test_call_driven_in_place.py)。速さは負荷で揺れるので検で固定せず、歩の数で見る。
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
    """引数の数を問わずに ``Call`` を作る — 引数の数の誤りは VM が関数を呼ぶ時に上がる。"""
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
