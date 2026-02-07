"""Tests for @do decorator when applied to class and instance methods."""

from __future__ import annotations

import inspect
from collections.abc import Generator
from typing import Any

from doeff import Program, do


def _as_generator(program: Any) -> Generator[Any, Any, Any]:
    from doeff.program import KleisliProgramCall

    if hasattr(program, "to_generator"):
        return program.to_generator()

    if isinstance(program, KleisliProgramCall):
        kernel = program.execution_kernel
        if kernel is None and program.kleisli_source is not None:
            kernel = getattr(program.kleisli_source, "func", None)
        if kernel is None:
            raise TypeError("Execution kernel unavailable for KleisliProgramCall")
        result = kernel(*program.args, **program.kwargs)
        if isinstance(result, Generator):
            return result

        def _pure_result() -> Generator[Any, Any, Any]:
            return result
            yield  # pragma: no cover

        return _pure_result()

    raise TypeError(f"Unsupported program-like object: {type(program).__name__}")


def _run_program(program: Program[Any]) -> Any:
    """Execute a program that does not yield external effects."""

    stack = [_as_generator(program)]
    sentinel: Any | None = None

    while stack:
        current = stack[-1]
        try:
            if sentinel is None:
                yielded = next(current)
            else:
                yielded = current.send(sentinel)
                sentinel = None
        except StopIteration as exc:
            stack.pop()
            sentinel = exc.value
            continue

        from doeff.program import KleisliProgramCall

        if isinstance(yielded, (Program, KleisliProgramCall)):
            stack.append(_as_generator(yielded))
            continue

        raise AssertionError(
            f"Program yielded unsupported value {yielded!r}; these tests expect only nested Programs."
        )

    return sentinel


def test_do_instance_method_signature_and_execution() -> None:
    class Counter:
        def __init__(self, base: int) -> None:
            self.base = base

        @do
        def increment(self, delta: int) -> Generator[Program[Any], Any, int]:
            return self.base + delta

    counter = Counter(3)

    # Access via class retains `self` parameter.
    class_sig = inspect.signature(Counter.increment)
    assert list(class_sig.parameters.keys()) == ["self", "delta"]

    # Access via instance should behave like a bound method with no explicit self.
    bound = counter.increment
    bound_sig = inspect.signature(bound)
    assert list(bound_sig.parameters.keys()) == ["delta"]

    from doeff.program import KleisliProgramCall

    program = bound(4)
    assert isinstance(program, (Program, KleisliProgramCall))
    assert _run_program(program) == 7


def test_do_class_method_signature_and_execution() -> None:
    class Aggregator:
        bias = 2

        @classmethod
        @do
        def produce(cls, value: int) -> Generator[Program[Any], Any, int]:
            return cls.bias + value

    from doeff.program import KleisliProgramCall

    class_sig = inspect.signature(Aggregator.produce)
    assert list(class_sig.parameters.keys()) == ["value"]

    program = Aggregator.produce(5)
    assert isinstance(program, (Program, KleisliProgramCall))
    assert _run_program(program) == 7


def test_do_static_method_signature_and_execution() -> None:
    class Math:
        @staticmethod
        @do
        def double(value: int) -> Generator[Program[Any], Any, int]:
            return value * 2

    from doeff.program import KleisliProgramCall

    class_sig = inspect.signature(Math.double)
    assert list(class_sig.parameters.keys()) == ["value"]

    math = Math()
    bound_sig = inspect.signature(math.double)
    assert list(bound_sig.parameters.keys()) == ["value"]

    program = math.double(3)
    assert isinstance(program, (Program, KleisliProgramCall))
    assert _run_program(program) == 6
