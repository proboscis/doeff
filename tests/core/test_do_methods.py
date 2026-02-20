from __future__ import annotations

import inspect
from typing import Any

import doeff_vm

from doeff import Program, default_handlers, do, run


def _run_program(program: Program[Any]) -> Any:
    return run(program, handlers=default_handlers()).value


def test_do_instance_method_signature_and_execution() -> None:
    class Counter:
        def __init__(self, base: int) -> None:
            self.base = base

        @do
        def increment(self, delta: int):
            if False:  # pragma: no cover
                yield Program.pure(None)
            return self.base + delta

    counter = Counter(3)

    class_sig = inspect.signature(Counter.increment)
    assert list(class_sig.parameters.keys()) == ["self", "delta"]

    bound = counter.increment
    bound_sig = inspect.signature(bound)
    assert list(bound_sig.parameters.keys()) == ["delta"]

    program = bound(4)
    assert isinstance(program, Program)
    assert _run_program(program) == 7


def test_do_class_method_signature_and_execution() -> None:
    class Aggregator:
        bias = 2

        @classmethod
        @do
        def produce(cls, value: int):
            if False:  # pragma: no cover
                yield Program.pure(None)
            return cls.bias + value

    class_sig = inspect.signature(Aggregator.produce)
    assert list(class_sig.parameters.keys()) == ["value"]

    program = Aggregator.produce(5)
    assert isinstance(program, Program)
    assert _run_program(program) == 7


def test_do_static_method_signature_and_execution() -> None:
    class Math:
        @staticmethod
        @do
        def double(value: int):
            if False:  # pragma: no cover
                yield Program.pure(None)
            return value * 2

    class_sig = inspect.signature(Math.double)
    assert list(class_sig.parameters.keys()) == ["value"]

    math = Math()
    bound_sig = inspect.signature(math.double)
    assert list(bound_sig.parameters.keys()) == ["value"]

    program = math.double(3)
    assert isinstance(program, Program)
    assert _run_program(program) == 6


def test_do_generator_wrapper_wraps_bridge_generator() -> None:
    @do
    def sample():
        yield Program.pure(1)
        return 2

    wrapper = sample.func()
    assert isinstance(wrapper, doeff_vm.DoeffGenerator)
    assert inspect.isgenerator(wrapper.generator)
    assert not hasattr(wrapper.generator, "__doeff_inner__")
