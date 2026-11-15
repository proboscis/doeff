"""Functions doeff makes at run time get their names when made (doeff._named).

On the free-threaded build, writing a function's ``__name__``, ``__qualname__``, ``__doc__`` or
``__module__`` stalls the writer every few hundred writes until a thread inside a long C call
reaches a point where it can be paused. doeff made ``@do`` wrappers and handler installers that way
on the scheduler thread for every ``on-raise`` and ``absent-as`` it ran, and a turn host spent a
quarter to a half of that thread's time waiting. These tests keep the names the same as
``functools.update_wrapper`` gave, measure that making them no longer waits for another thread's
C call, and keep the runtime modules from writing the names again.
"""

import functools
import re
import sys
import threading
import time
from pathlib import Path

import pytest

from doeff import do
from doeff._named import TEMPLATE_DOC, named, wrapped_as
from doeff_core_effects._outcomes import RaiseCase, absent_as, on_raise

ROOT = Path(__file__).resolve().parents[1]


def _module_level(x: int) -> int:
    """documentation of the wrapped function"""
    return x


def test_wrapped_as_gives_the_names_update_wrapper_gives() -> None:
    def template(*args: object) -> object:
        """(documentation given at run time by doeff._named)"""
        return args

    def plain(*args: object) -> object:
        return args

    made = wrapped_as(template, _module_level)
    expected = functools.update_wrapper(plain, _module_level)
    for attr in (*functools.WRAPPER_ASSIGNMENTS, "__wrapped__"):
        assert getattr(made, attr) == getattr(expected, attr), attr
    assert made(1, 2) == (1, 2)


def test_do_names_a_def_made_inside_a_call_as_wraps_did() -> None:
    def outer() -> object:
        @do
        def inner() -> object:
            yield None

        return inner

    inner = outer()
    assert inner.__name__ == "inner"
    assert inner.__qualname__.endswith("outer.<locals>.inner")
    assert inner.__module__ == __name__
    assert inner.__doc__ is None
    assert inner.__wrapped__.__name__ == "inner"


def test_boundary_installers_keep_their_names() -> None:
    class Refused:
        pass

    raised = on_raise(RaiseCase((Refused,), lambda reason: reason))
    absent = absent_as(None)
    assert (raised.__name__, raised.__qualname__) == ("on_raise", "on_raise")
    assert (absent.__name__, absent.__qualname__) == ("absent_as", "absent_as")
    assert raised._doeff_is_handler_fn is True


def test_named_refuses_a_documentation_without_the_template_docstring() -> None:
    def bare() -> None:
        pass

    if sys.flags.optimize >= 2:
        pytest.skip("docstrings are dropped under -OO")
    with pytest.raises(ValueError, match="TEMPLATE_DOC"):
        named(bare, "bare", doc="a documentation")


def test_named_refuses_another_module_for_a_function_reading_globals() -> None:
    def reads() -> object:
        """(documentation given at run time by doeff._named)"""
        return Path

    with pytest.raises(ValueError, match="reads globals"):
        named(reads, "reads", module="elsewhere")


@pytest.mark.skipif(
    getattr(sys, "_is_gil_enabled", lambda: True)(), reason="the stall is of the free-threaded build"
)
def test_making_handlers_does_not_wait_for_another_threads_c_call() -> None:
    data = list(range(1_500_000, 0, -1))
    done = threading.Event()
    sorting_now = threading.Event()

    def sorting() -> None:
        sorting_now.set()
        while not done.is_set():
            sorted(data)  # a long C call: the thread can be paused only when it ends

    started = time.perf_counter()
    sorted(data)
    one_sort = time.perf_counter() - started
    worker = threading.Thread(target=sorting, daemon=True)
    worker.start()
    try:
        sorting_now.wait()

        class Refused:
            pass

        case = RaiseCase((Refused,), lambda reason: reason)
        started = time.perf_counter()
        for _ in range(2000):
            on_raise(case)
            absent_as(None)
        took = time.perf_counter() - started
    finally:
        done.set()
        worker.join()
    # Writing the names stalled about once in 50 handlers for part of a sort (2000 of each took
    # 10 seconds and more); made under their names, the 4000 take a few hundredths of a second.
    assert took < max(1.0, 3 * one_sort), (took, one_sort)


_NAME_WRITE = re.compile(
    r"\.__(name|qualname|doc|module)__\s*=(?!=)|setv\s+[\w.-]+\.__(name|qualname|doc|module)__"
)
# The modules that make functions while a program runs (installers, @do wrappers, boundaries).
_RUNTIME_MODULES = (
    "doeff/do.py",
    "doeff/program.py",
    "packages/doeff-core-effects/doeff_core_effects/_outcomes.py",
    "packages/doeff-core-effects/doeff_core_effects/_http_handlers_impl.hy",
)


@pytest.mark.parametrize("relative", _RUNTIME_MODULES)
def test_runtime_modules_do_not_write_function_names(relative: str) -> None:
    text = (ROOT / relative).read_text()
    found = [line.strip() for line in text.splitlines() if _NAME_WRITE.search(line)]
    assert not found, f"{relative} writes a function's names; make it with doeff._named: {found}"
