"""生成器の ``@do`` の定義を呼ぶ 1 回(``Call``)の往復の形と答え(agora-redesign #2801)。

VM は yield された ``Call`` を、生成器の定義ならその場で関数を呼んで stream にし(``Expand(Pure(stream))`` —
``@do`` の handler と同じ形)、生成器は C API の ``PyIter_Send`` で回す(StopIteration の例外を作らない)。
答え(値・None・例外の捕まる所)は前と同じで、往復の VM の歩は 3 つ: 親へ送って ``Call`` を受ける・stream を積む・
子へ送って終わる(前は ``Expand(Apply(Pure(thunk)))`` を通る 5 つ)。速さは負荷で揺れるので検で固定せず、歩の数で見る。
"""

import pytest

from doeff import do, run
from doeff_vm import PyVM


class Boom(Exception):
    pass


@do
def leaf(x: int):
    if False:
        yield
    return x + 1


@do
def returns_none():
    if False:
        yield


@do
def raises(x: int):
    if False:
        yield
    raise Boom(x)


@do
def calls(n: int):
    total = 0
    for i in range(n):
        total += yield leaf(i)
    return total


def _steps_and_value(program: object) -> tuple[int, object]:
    vm = PyVM()
    value = vm.run(program)
    return vm.step_count(), value


def test_a_call_of_a_generator_definition_round_trips_in_three_vm_steps() -> None:
    steps_10, value_10 = _steps_and_value(calls(10))
    steps_20, value_20 = _steps_and_value(calls(20))
    assert (value_10, value_20) == (sum(range(1, 11)), sum(range(1, 21)))
    assert (steps_20 - steps_10) / 10 == 3


def test_a_generator_definition_returning_none_answers_none() -> None:
    @do
    def parent():
        value = yield returns_none()
        return ("got", value)

    assert run(parent()) == ("got", None)


def test_an_exception_of_the_called_definition_is_caught_where_the_caller_yields() -> None:
    @do
    def parent():
        try:
            yield raises(1)
        except Boom as error:
            return ("caught", error.args)
        return "not caught"

    assert run(parent()) == ("caught", (1,))


def test_a_wrong_argument_count_is_a_type_error_the_caller_catches() -> None:
    too_many: tuple[int, ...] = (1, 2)

    @do
    def parent():
        try:
            yield leaf(*too_many)
        except TypeError:
            return "caught"
        return "not caught"

    assert run(parent()) == "caught"


def test_an_uncaught_exception_of_the_called_definition_leaves_run() -> None:
    @do
    def parent():
        yield raises(2)
        return "not raised"

    with pytest.raises(Boom):
        run(parent())
