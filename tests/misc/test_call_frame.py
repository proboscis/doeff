"""Tests for CallFrame and program_call_stack in ExecutionContext."""

import pytest

from doeff import CESKInterpreter, do
from doeff.effects import Ask, Pure, ProgramCallFrame, ProgramCallStack
from doeff.program import Program
from doeff.types import CallFrame, ExecutionContext


def test_execution_context_has_program_call_stack():
    """ExecutionContext should have program_call_stack field."""
    ctx = ExecutionContext()

    assert hasattr(ctx, "program_call_stack")
    assert isinstance(ctx.program_call_stack, list)
    assert len(ctx.program_call_stack) == 0


def test_execution_context_copy_preserves_call_stack():
    """ExecutionContext.copy() should preserve program_call_stack."""
    from doeff.kleisli import KleisliProgram

    def gen_func():
        return (yield Pure(1))

    kleisli = KleisliProgram(func=gen_func)

    frame = CallFrame(
        kleisli=kleisli,
        function_name="test_func",
        args=(),
        kwargs={},
        depth=0,
        created_at=None
    )

    ctx = ExecutionContext(program_call_stack=[frame])
    ctx_copy = ctx.copy()

    # Stack should be copied (not same list reference)
    assert ctx_copy.program_call_stack is not ctx.program_call_stack
    # But should have same content
    assert len(ctx_copy.program_call_stack) == 1
    assert ctx_copy.program_call_stack[0] is frame


def test_call_frame_structure():
    """CallFrame should hold metadata about a KleisliProgram call."""
    from doeff.kleisli import KleisliProgram

    def gen_func(x, y):
        return (yield Pure(x + y))

    kleisli = KleisliProgram(func=gen_func)

    frame = CallFrame(
        kleisli=kleisli,
        function_name="my_func",
        args=(5, 10),
        kwargs={"extra": "value"},
        depth=2,
        created_at=None
    )

    assert frame.kleisli is kleisli
    assert frame.function_name == "my_func"
    assert frame.args == (5, 10)
    assert frame.kwargs == {"extra": "value"}
    assert frame.depth == 2
    assert frame.created_at is None


def test_call_frame_immutable():
    """CallFrame should be frozen/immutable."""
    from doeff.kleisli import KleisliProgram

    def gen_func():
        return (yield Pure(1))

    kleisli = KleisliProgram(func=gen_func)
    frame = CallFrame(
        kleisli=kleisli,
        function_name="test",
        args=(),
        kwargs={},
        depth=0,
        created_at=None
    )

    # Should not be able to modify
    with pytest.raises(AttributeError, match=r"can't set attribute|cannot assign to field"):
        frame.depth = 5  # type: ignore


@pytest.mark.asyncio
async def test_execution_context_initialized_with_empty_call_stack():
    """Execution should start with empty call stack."""
    interpreter = CESKInterpreter()

    @do
    def simple_program() -> Program[int]:
        value = yield Pure(42)
        return value

    result = await interpreter.run_async(simple_program())

    # Call stack should exist and be empty after execution completes
    assert hasattr(result.context, "program_call_stack")
    assert isinstance(result.context.program_call_stack, list)
    # Should be empty since simple programs don't push frames yet (Phase 4 feature)
    assert len(result.context.program_call_stack) == 0


@pytest.mark.asyncio
async def test_effect_observation_includes_call_stack_snapshot():
    """Effect observations should capture the current program call stack."""

    interpreter = CESKInterpreter()

    @do
    def inner() -> Program[int]:
        base = yield Pure(1)
        env_value = yield Ask("value")
        return base + env_value

    @do
    def outer() -> Program[int]:
        return (yield inner())

    ctx = ExecutionContext(env={"value": 2})
    run_result = await interpreter.run_async(outer(), ctx)

    assert run_result.is_ok
    observations = run_result.context.effect_observations
    assert observations, "Expected effect observations to be recorded"

    # Find the Ask observation and inspect its call stack snapshot
    ask_observation = next(
        obs for obs in observations if obs.effect_type == "Ask" and obs.key == "value"
    )

    snapshot = ask_observation.call_stack_snapshot
    assert snapshot, "Call stack snapshot should not be empty"
    assert snapshot[0].function_name == "outer"
    assert snapshot[1].function_name == "inner"
    assert snapshot[0].depth == 0
    assert snapshot[1].depth == 1

    # Ensure call stack has been cleaned up after execution
    assert len(run_result.context.program_call_stack) == 0


def test_execution_context_with_initial_call_stack():
    """ExecutionContext can be created with initial call stack."""
    from doeff.kleisli import KleisliProgram

    def gen_func():
        return (yield Pure(1))

    kleisli = KleisliProgram(func=gen_func)
    frame1 = CallFrame(
        kleisli=kleisli,
        function_name="func1",
        args=(),
        kwargs={},
        depth=0,
        created_at=None
    )
    frame2 = CallFrame(
        kleisli=kleisli,
        function_name="func2",
        args=(1, 2),
        kwargs={},
        depth=1,
        created_at=None
    )

    ctx = ExecutionContext(program_call_stack=[frame1, frame2])

    assert len(ctx.program_call_stack) == 2
    assert ctx.program_call_stack[0] is frame1
    assert ctx.program_call_stack[1] is frame2


def test_call_frame_with_creation_context():
    """CallFrame should support EffectCreationContext."""
    from doeff.kleisli import KleisliProgram
    from doeff.types import EffectCreationContext

    def gen_func():
        return (yield Pure(1))

    kleisli = KleisliProgram(func=gen_func)
    creation_ctx = EffectCreationContext(
        filename="test.py",
        line=42,
        function="test_function"
    )

    frame = CallFrame(
        kleisli=kleisli,
        function_name="my_func",
        args=(),
        kwargs={},
        depth=0,
        created_at=creation_ctx
    )

    assert frame.created_at is creation_ctx
    assert frame.created_at.filename == "test.py"
    assert frame.created_at.line == 42


@pytest.mark.asyncio
async def test_program_call_stack_effects():
    """ProgramCallStack and ProgramCallFrame should reflect nested calls."""

    interpreter = CESKInterpreter()

    @do
    def inner() -> Program[list[str]]:
        stack = yield ProgramCallStack()
        current = yield ProgramCallFrame()
        parent = yield ProgramCallFrame(1)
        names = [frame.function_name for frame in stack]
        return [
            ",".join(names),
            current.function_name,
            parent.function_name,
        ]

    @do
    def outer() -> Program[list[str]]:
        return (yield inner())

    result = await interpreter.run_async(outer())

    assert result.is_ok
    names, current_name, parent_name = result.value

    assert names.startswith("outer,inner")
    assert current_name == "inner"
    assert parent_name == "outer"
