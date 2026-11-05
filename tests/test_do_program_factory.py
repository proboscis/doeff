"""Cost of a single ``@do`` decoration (``program_factory``) and the guarantee that its answer is unchanged.

``@do`` is applied to handler closures on every call (handlers built per example -- 1811 times per test in the #2421 measurement). Each decoration
used ``inspect.isgeneratorfunction`` to strip partialmethod / method / partial and imported ``doeff_vm`` inside the function.
Plain functions now read the code flag directly, and the import was moved to the top of the module. This test checks:

- The answer equals ``inspect.isgeneratorfunction`` (including shapes that have something to strip).
- The decorated function's behavior is unchanged (a generator runs its body; a non-generator yields its value).
- Decorating a plain function neither calls ``inspect.isgeneratorfunction`` nor imports in ``program_factory``
  (count-based tests that turn red if the fix is removed).
"""

from __future__ import annotations

import dis
import functools
import importlib
import inspect
import itertools
from collections.abc import Callable, Generator

import pytest
from doeff_vm import Call, DoFunction

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
    """Plain function carrying the partialmethod marker ``__partialmethod__`` -- inspect reads the flag of the function the marker points to."""

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
    """Failing-case pair (count-based): decorating a plain function does not go through ``inspect.isgeneratorfunction``.

    If the fix is removed (``program_factory`` calls ``inspect.isgeneratorfunction``), decorating 3 closures counts 3 calls.
    Shapes that have something to strip (partial) are still left to inspect, as before.
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
    """Failing-case pair (count-based): a single call of a ``@do`` function does not call the ``Call`` type -- the definition's ``make_call``
    (a vectorcall method) builds the same ``Call``. Calling the type incurred the args tuple, the ``tp_new`` dispatch, and the pyo3
    constructor's argument parsing on every call. If the fix is removed, it counts 1 call.
    """
    do_module = importlib.import_module("doeff.do")
    constructed = itertools.count()

    def counting(
        function: DoFunction, args: tuple[object, ...], kwargs: dict[str, object]
    ) -> Call[object, object]:
        next(constructed)
        return Call(function, args, kwargs)

    # If the wrapper looked up the module-level name Call on every call and called the type, the object injected here would count it
    # (do.py after the fix does not import Call)
    monkeypatch.setattr(do_module, "Call", counting, raising=False)
    decorated = do(_generator)
    program = decorated(1)
    keyword_program = decorated(x=1)
    assert isinstance(program, Call)
    assert isinstance(keyword_program, Call)
    assert (program.args, program.kwargs) == ((1,), {})
    assert (keyword_program.args, keyword_program.kwargs) == ((), {"x": 1})
    assert program.function is keyword_program.function
    assert (run(program), run(keyword_program)) == (2, 2)
    assert next(constructed) == 0


def test_program_factory_does_not_import_per_decoration() -> None:
    """Decorating does not go through the import machinery (``_handle_fromlist``) -- the name is looked up only once, at the top of the module."""
    opnames = {instruction.opname for instruction in dis.get_instructions(program_factory)}
    assert "IMPORT_NAME" not in opnames
