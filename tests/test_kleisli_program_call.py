"""Tests for KleisliProgramCall - bound function with args (partial application)."""

import pytest

from doeff import ProgramInterpreter
from doeff.effects import Ask, Pure
from doeff.program import KleisliProgramCall, _AutoUnwrapStrategy
from doeff.types import EffectCreationContext, ExecutionContext, Ok


def _make_call(gen_func, *args, **kwargs):
    return KleisliProgramCall(
        kleisli_source=None,
        args=tuple(args),
        kwargs=dict(kwargs),
        function_name=getattr(gen_func, "__name__", "<anonymous>"),
        created_at=None,
        auto_unwrap_strategy=_AutoUnwrapStrategy(),
        execution_kernel=gen_func,
    )


def test_kleisli_program_call_structure():
    """KleisliProgramCall should hold generator function + bound args."""

    def gen_func(x, y):
        value = yield Pure(x + y)
        return value

    kpcall = _make_call(gen_func, 5, 10)

    assert kpcall.execution_kernel is gen_func
    assert kpcall.args == (5, 10)
    assert kpcall.kwargs == {}
    assert kpcall.kleisli_source is None
    assert kpcall.function_name == "gen_func"
    assert kpcall.created_at is None


def test_kleisli_program_call_to_generator():
    """to_generator() should create generator by calling function with args."""

    def gen_func(x, y):
        value = yield Pure(x + y)
        return value

    kpcall = _make_call(gen_func, 5, 10)

    # to_generator() should call gen_func(5, 10)
    gen = kpcall.to_generator()

    # Check it's a generator
    assert hasattr(gen, "__next__")
    assert hasattr(gen, "send")

    # First yield should be Pure(15)
    first_yield = next(gen)
    assert isinstance(first_yield, type(Pure(15)))
    assert first_yield.value == 15


@pytest.mark.asyncio
async def test_kleisli_program_call_execution():
    """KleisliProgramCall should be executable by interpreter."""

    def gen_func(x, y):
        value = yield Pure(x * y)
        return value

    kpcall = _make_call(gen_func, 7, 6)

    interpreter = ProgramInterpreter()
    result = await interpreter.run_async(kpcall)

    assert result.result == Ok(42)
    assert result.value == 42


@pytest.mark.asyncio
async def test_kleisli_program_call_with_kwargs():
    """KleisliProgramCall should handle keyword arguments."""

    def gen_func(base, multiplier=2):
        value = yield Pure(base * multiplier)
        return value

    kpcall = _make_call(gen_func, 10, multiplier=5)

    interpreter = ProgramInterpreter()
    result = await interpreter.run_async(kpcall)

    assert result.value == 50


def test_create_from_kleisli_constructor():
    """create_from_kleisli should store source KleisliProgram metadata."""
    from doeff.kleisli import KleisliProgram

    def gen_func(x):
        return (yield Pure(x))

    kleisli = KleisliProgram(func=gen_func)

    kpcall = KleisliProgramCall.create_from_kleisli(
        kleisli=kleisli,
        args=(42,),
        kwargs={},
        function_name="test_func",
        created_at=None
    )

    assert callable(kpcall.execution_kernel)
    assert kpcall.execution_kernel is gen_func
    assert kpcall.kleisli_source is kleisli
    assert kpcall.args == (42,)
    assert kpcall.function_name == "test_func"

    gen = kpcall.to_generator()
    first = next(gen)
    assert getattr(first, "value", None) == 42


def test_create_derived_preserves_metadata():
    """create_derived should preserve metadata from parent KPCall."""
    from doeff.kleisli import KleisliProgram

    def original_func(x):
        return (yield Pure(x))

    def derived_func(x):
        return (yield Pure(x * 2))

    kleisli = KleisliProgram(func=original_func)
    creation_ctx = EffectCreationContext(
        filename="test.py",
        line=10,
        function="test"
    )

    parent = KleisliProgramCall.create_from_kleisli(
        kleisli=kleisli,
        args=(5,),
        kwargs={},
        function_name="original",
        created_at=creation_ctx
    )

    derived = KleisliProgramCall.create_derived(
        generator_func=derived_func,
        parent=parent,
        args=None,  # Keep parent args
        kwargs=None
    )

    # Metadata should be preserved
    assert derived.kleisli_source is kleisli
    assert derived.function_name == "original"
    assert derived.created_at is creation_ctx
    assert derived.args == (5,)  # Preserved from parent
    assert derived.kwargs == {}

    # Execution kernel should be the new derived function
    assert derived.execution_kernel is derived_func


def test_create_derived_can_override_args():
    """create_derived with explicit args should use those instead of parent."""

    def original_func(x):
        return (yield Pure(x))

    def derived_func(x):
        return (yield Pure(x * 2))

    parent = _make_call(original_func, 5, key="value")

    derived = KleisliProgramCall.create_derived(
        generator_func=derived_func,
        parent=parent,
        args=(10,),  # Override
        kwargs={"key": "new"}  # Override
    )

    assert derived.args == (10,)
    assert derived.kwargs == {"key": "new"}


@pytest.mark.asyncio
async def test_kleisli_program_call_with_effects():
    """KleisliProgramCall should work with complex effect compositions."""

    def gen_func(name):
        greeting = yield Ask("greeting")
        full_message = yield Pure(f"{greeting}, {name}!")
        return full_message

    kpcall = _make_call(gen_func, "World")

    interpreter = ProgramInterpreter()
    ctx = ExecutionContext(env={"greeting": "Hello"})
    result = await interpreter.run_async(kpcall, ctx)

    assert result.value == "Hello, World!"


def test_kleisli_program_call_mutable():
    """KleisliProgramCall remains mutable for debugging scenarios."""

    def gen_func():
        return (yield Pure(1))

    kpcall = _make_call(gen_func)

    kpcall.args = (1, 2, 3)
    assert kpcall.args == (1, 2, 3)


@pytest.mark.asyncio
async def test_kleisli_program_call_nested():
    """KleisliProgramCall should handle nested program calls."""

    def inner_gen(x):
        value = yield Pure(x * 2)
        return value

    def outer_gen(x):
        inner_result = yield _make_call(inner_gen, x)
        final = yield Pure(inner_result + 1)
        return final

    kpcall = _make_call(outer_gen, 5)

    interpreter = ProgramInterpreter()
    result = await interpreter.run_async(kpcall)

    # Should be (5 * 2) + 1 = 11
    assert result.value == 11
