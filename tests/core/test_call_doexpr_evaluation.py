from __future__ import annotations

import doeff_vm

from doeff import Apply, Ask, Effect, Get, KleisliProgram, Program, Pure, default_handlers, do, run
from doeff import ProgramBase


def _meta(fn):
    code = fn.__code__
    return {
        "function_name": code.co_name,
        "source_file": code.co_filename,
        "source_line": code.co_firstlineno,
    }


def test_run_resolves_plain_value_arg_before_kernel_call() -> None:
    @do
    def add_one(x: int):
        return x + 1

    result = run(add_one(41), handlers=default_handlers())
    assert result.value == 42


def test_run_resolves_ask_arg_before_kernel_call() -> None:
    @do
    def render(v: str):
        return f"v={v}"

    result = run(render(Ask("key")), handlers=default_handlers(), env={"key": "abc"})
    assert result.value == "v=abc"


def test_run_resolves_get_arg_before_kernel_call() -> None:
    @do
    def plus_two(v: int):
        return v + 2

    result = run(plus_two(Get("counter")), handlers=default_handlers(), store={"counter": 5})
    assert result.value == 7


def test_run_resolves_inner_program_arg_before_kernel_call() -> None:
    @do
    def inner():
        return 10

    @do
    def double(v: int):
        return v * 2

    result = run(double(inner()), handlers=default_handlers())
    assert result.value == 20


def test_run_resolves_nested_call_expressions_left_to_right() -> None:
    @do
    def inner(v: int):
        return v + 1

    @do
    def outer(v: int):
        return v * 3

    result = run(outer(inner(Ask("key"))), handlers=default_handlers(), env={"key": 4})
    assert result.value == 15


def test_program_annotated_arg_arrives_as_program_not_wrapped_doexpr() -> None:
    seen: dict[str, object] = {}

    @do
    def inner():
        return 7

    @do
    def keep_program(p: Program[int]):
        seen["p"] = p
        return hasattr(p, "to_generator")

    result = run(keep_program(inner()), handlers=default_handlers())
    assert result.value is True
    assert type(seen["p"]).__name__ != "Pure"


def test_apply_delivers_program_result_as_value() -> None:
    @do
    def inner():
        return 10

    def make_program():
        return inner()

    result = run(Apply(Pure(make_program), [], {}, _meta(make_program)), handlers=[])
    assert isinstance(result.value, ProgramBase)


def test_apply_delivers_effect_result_as_value() -> None:
    expected = Ask("token")

    def make_effect():
        return expected

    result = run(
        Apply(Pure(make_effect), [], {}, _meta(make_effect)),
        handlers=default_handlers(),
        env={"token": "secret"},
    )
    assert type(result.value) is type(expected)
    assert result.value.key == "token"


def test_handler_return_delivers_effect_result_as_value() -> None:
    expected = Ask("token")

    class Echo(doeff_vm.EffectBase):
        def __init__(self, value: int):
            self.value = value

    @do
    def handler(effect: Effect, k):
        if isinstance(effect, Echo):
            _ = yield doeff_vm.Resume(k, effect.value)
            return expected
        yield doeff_vm.Pass()

    @do
    def body():
        value = yield Echo(7)
        return value

    result = run(
        doeff_vm.WithHandler(handler, body()),
        handlers=default_handlers(),
        env={"token": "secret"},
    )
    assert type(result.value) is type(expected)
    assert result.value.key == "token"


def test_manual_kleisli_program_call_uses_expand_for_program_results() -> None:
    kleisli = KleisliProgram(lambda: Pure(33))

    result = run(kleisli(), handlers=[])

    assert result.value == 33
