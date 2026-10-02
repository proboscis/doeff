"""``@do`` の 1 回の飾りの費用(``program_factory``)と、その答えが変わらないこと(agora-redesign #2421)。

``@do`` は handler の閉包にも呼びごとに当たる(例ごとに作る handler — #2421 の測りで 1 検 1811 回)。飾るたびに
``inspect.isgeneratorfunction`` で partialmethod・method・partial を剥がし、関数の中で ``doeff_vm`` を import していた。
素の関数は code の旗を直に読み、import は module の頭へ移した。ここでは:

- 答えが ``inspect.isgeneratorfunction`` と同じこと(剥がす物のある形も含めて)。
- 飾った関数の振る舞い(生成器なら本体を走らせる・生成器でなければ値)が変わらないこと。
- 素の関数を飾る時に ``inspect.isgeneratorfunction`` を呼ばず、``program_factory`` が import しないこと
  (直しを外すと赤になる数の検)。
"""

from __future__ import annotations

import dis
import functools
import importlib
import inspect
import itertools
from collections.abc import Callable, Generator

import pytest
from doeff_vm import Call

from doeff import Pure, do, run
from doeff.do import _is_generator_function, program_factory


def _generator(x: int) -> Generator[object, int, int]:
    value = yield Pure(x)
    return value + 1


def _plain(x: int) -> int:
    return x + 1


def _closure_generator() -> Callable[..., object]:
    offset = 2

    def inner(x: int) -> Generator[object, int, int]:
        value = yield Pure(x)
        return value + offset

    return inner


class _Holder:
    def method(self, x: int) -> Generator[object, int, int]:
        value = yield Pure(x)
        return value

    partial_method = functools.partialmethod(method, 1)

    def __call__(self, x: int) -> Generator[object, int, int]:
        value = yield Pure(x)
        return value


def _function_marked_as_partialmethod() -> Callable[..., object]:
    """partialmethod の印 ``__partialmethod__`` を持つ素の関数 — inspect は印の先の関数の旗を読む。"""

    def shim(x: int) -> int:
        return x

    shim.__dict__["__partialmethod__"] = functools.partialmethod(_generator)
    return shim


SHAPES: dict[str, Callable[..., object]] = {
    "generator function": _generator,
    "plain function": _plain,
    "closure generator": _closure_generator(),
    "lambda": lambda x: x,
    "partial of a generator": functools.partial(_generator, 1),
    "bound method": _Holder().method,
    "partialmethod on an instance": _Holder().partial_method,
    "callable instance": _Holder(),
    "builtin": len,
    "function marked as partialmethod": _function_marked_as_partialmethod(),
    "wrapped plain function": functools.wraps(_generator)(lambda x: x),
}


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_generator_flag_agrees_with_inspect(name: str) -> None:
    fn = SHAPES[name]
    assert _is_generator_function(fn) is inspect.isgeneratorfunction(fn), name


def test_decorated_functions_keep_their_behavior() -> None:
    assert run(do(_generator)(1)) == 2
    assert run(do(_plain)(1)) == 2
    assert run(do(_closure_generator())(1)) == 3


def test_decorating_a_plain_function_does_not_unwrap_through_inspect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """失敗ケースの対(数の検): 素の関数の飾りは ``inspect.isgeneratorfunction`` を通らない。

    直しを外すと(``program_factory`` が ``inspect.isgeneratorfunction`` を呼ぶ形)、閉包を 3 つ飾って 3 回数える。
    剥がす物のある形(partial)は今までどおり inspect に任せる。
    """
    calls = [0]
    original = inspect.isgeneratorfunction

    def counting(fn: object) -> bool:
        calls[0] += 1
        return original(fn)

    monkeypatch.setattr(inspect, "isgeneratorfunction", counting)
    for _ in range(3):
        decorated = do(_closure_generator())
        assert run(decorated(1)) == 3
    assert calls[0] == 0
    do(functools.partial(_generator))
    assert calls[0] == 1


def test_calling_a_decorated_function_builds_its_call_without_the_call_constructor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """失敗ケースの対(数の検): ``@do`` の関数の 1 回の呼びは ``Call`` の型を呼ばない — 定義の ``make_call``
    (vectorcall の method)が同じ ``Call`` を作る。型を呼ぶと、引数の tuple・``tp_new`` の振り分け・pyo3 の
    constructor の引数の読みが呼びごとに掛かった(agora-redesign #2817)。直しを外すと 1 回数える。
    """
    do_module = importlib.import_module("doeff.do")
    constructed = itertools.count()
    original = Call

    def counting(*args: object, **kwargs: object) -> object:
        next(constructed)
        return original(*args, **kwargs)

    # wrapper が呼びごとに module の名 Call を引いて型を呼ぶ形なら、ここで差し込んだ物が数える(直した後の do.py は Call を import しない)
    monkeypatch.setattr(do_module, "Call", counting, raising=False)
    decorated = do(_generator)
    program = decorated(1)
    keyword_program = decorated(x=1)
    assert isinstance(program, original)
    assert (program.args, program.kwargs) == ((1,), {})
    assert (keyword_program.args, keyword_program.kwargs) == ((), {"x": 1})
    assert program.function is keyword_program.function
    assert (run(program), run(keyword_program)) == (2, 2)
    assert next(constructed) == 0


def test_program_factory_does_not_import_per_decoration() -> None:
    """飾るたびに import の機構(``_handle_fromlist``)を通らない — 名は module の頭で 1 度だけ引く。"""
    opnames = {instruction.opname for instruction in dis.get_instructions(program_factory)}
    assert "IMPORT_NAME" not in opnames
