from __future__ import annotations

import inspect

import doeff_vm
import pytest

from doeff import Ask, Program, do
from doeff.do import _default_get_frame


def test_do_constructs_bridge_doeff_generator_with_bridge_callback() -> None:
    @do
    def sample():
        value = yield Ask("key")
        return str(value)

    wrapped = sample.func()

    assert isinstance(wrapped, doeff_vm.DoeffGenerator)
    assert inspect.isgenerator(wrapped.generator)
    assert wrapped.get_frame.__name__ == "_do_get_frame"
    assert wrapped.function_name == "sample"


def test_do_bridge_callback_returns_user_frame_not_bridge_frame() -> None:
    @do
    def sample():
        value = yield Ask("key")
        return str(value)

    wrapped = sample.func()
    yielded = next(wrapped.generator)
    assert isinstance(yielded, doeff_vm.PyAsk)
    user_frame = wrapped.get_frame(wrapped.generator)

    assert user_frame is not None
    assert user_frame.f_code.co_name == "sample"
    assert user_frame is not wrapped.generator.gi_frame


def test_do_bridge_callback_returns_none_after_exhaustion() -> None:
    @do
    def single_step():
        yield Ask("key")
        return 7

    wrapped = single_step.func()
    next(wrapped.generator)
    assert wrapped.get_frame(wrapped.generator) is not None

    with pytest.raises(StopIteration):
        next(wrapped.generator)

    assert wrapped.get_frame(wrapped.generator) is None


def test_program_to_generator_uses_default_callback() -> None:
    program = Program.first_some(Program.pure(1))
    wrapped = program.to_generator()

    assert isinstance(wrapped, doeff_vm.DoeffGenerator)
    assert wrapped.get_frame is _default_get_frame
    assert wrapped.get_frame(wrapped.generator) is wrapped.generator.gi_frame


def test_with_handler_wraps_generator_handler_returns_as_doeff_generator() -> None:
    def handler(effect, k):
        if False:  # pragma: no cover
            yield
        return doeff_vm.Delegate(effect)

    control = doeff_vm.WithHandler(handler, doeff_vm.Perform(Ask("x")))
    wrapped = control.handler(Ask("x"), None)

    assert isinstance(wrapped, doeff_vm.DoeffGenerator)
    assert inspect.isgenerator(wrapped.generator)
    assert wrapped.get_frame is _default_get_frame
    assert wrapped.function_name == "handler"
