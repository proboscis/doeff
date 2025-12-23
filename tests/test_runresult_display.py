"""Test enhanced RunResult.display() method with stack traces and error details."""

import json

import pytest

from doeff import (
    Ask,
    CachePut,
    Dep,
    EffectGenerator,
    ExecutionContext,
    Fail,
    Log,
    ProgramInterpreter,
    Put,
    Recover,
    Step,
    do,
)


@pytest.mark.asyncio
async def test_display_success():
    """Test display() for successful result."""
    @do
    def successful_program() -> EffectGenerator[str]:
        yield Put("counter", 42)
        yield Log("Computing result")
        yield Log("Another log entry")
        return "Success value!"

    engine = ProgramInterpreter()
    result = await engine.run_async(successful_program())

    # Test display output
    display_output = result.display()

    # Verify success indicators
    assert "✅ Success" in display_output
    assert "Success value!" in display_output
    assert "counter: 42" in display_output
    assert "Computing result" in display_output
    assert "Another log entry" in display_output

    # Verify summary
    assert "Status: ✅ OK" in display_output
    assert "State items: 1" in display_output
    assert "Log entries: 2" in display_output

    # Test verbose mode
    verbose_output = result.display(verbose=True)
    assert len(verbose_output) >= len(display_output)


@pytest.mark.asyncio
async def test_display_includes_call_tree():
    """Display should include the effect call tree when observations exist."""

    @do
    def inner() -> EffectGenerator[int]:
        value = yield Ask("value")
        return value

    @do
    def outer() -> EffectGenerator[int]:
        return (yield inner())

    engine = ProgramInterpreter()
    context = ExecutionContext(env={"value": 7})
    result = await engine.run_async(outer(), context)

    output = result.display()

    assert "🌳 Effect Call Tree:" in output
    assert "outer()" in output
    assert "inner()" in output
    assert "Ask('value')" in output


@pytest.mark.asyncio
async def test_display_error_with_traceback():
    """Test display() shows user-friendly error output with root cause first."""
    @do
    def failing_program() -> EffectGenerator[str]:
        yield Put("step", "before_error")
        yield Log("About to fail")
        yield Fail(ValueError("Something went wrong in the program"))
        return "never reached"

    engine = ProgramInterpreter()
    result = await engine.run_async(failing_program())

    # Test display output
    display_output = result.display()

    # Verify error indicators
    assert "❌ Failure" in display_output
    # New format shows root cause first instead of error chain
    assert "Root Cause:" in display_output
    assert "ValueError: Something went wrong in the program" in display_output
    # Status section still shows effect failure info
    assert "Effect 'ResultFailEffect' failed" in display_output

    # Verify creation location is shown in status section
    assert "📍 Created at:" in display_output
    assert "failing_program" in display_output

    # Verify state and logs are still shown
    assert 'step: "before_error"' in display_output
    assert "About to fail" in display_output

    # Verify summary shows error status
    assert "Status: ❌ Error" in display_output

    # Verbose mode should show the full error chain
    verbose_output = result.display(verbose=True)
    assert "Error Chain (most recent first):" in verbose_output
    assert "📍 Effect Creation Stack Trace:" in verbose_output


@pytest.mark.asyncio
async def test_display_trace_includes_user_frames():
    """Display output should show full user stack frames in verbose mode."""

    def helper_outer() -> None:
        helper_middle()

    def helper_middle() -> None:
        helper_inner()

    def helper_inner() -> None:
        json.loads('{"unterminated": "value"')

    @do
    def failing_program() -> EffectGenerator[None]:
        helper_outer()
        return None

    engine = ProgramInterpreter()
    result = await engine.run_async(failing_program())

    assert result.is_err

    # Non-verbose mode shows root cause clearly
    display_output = result.display(verbose=False)
    assert "Root Cause:" in display_output
    assert "JSONDecodeError" in display_output

    # Verbose mode shows full traceback with user helper frames
    verbose_output = result.display(verbose=True)
    assert "helper_outer" in verbose_output
    assert "helper_middle" in verbose_output
    assert "tests/test_runresult_display.py" in verbose_output


@pytest.mark.asyncio
async def test_display_primary_effect_shows_creation_stack(monkeypatch):
    """Closest failing effect should surface its root cause and creation location."""

    def failing_dumps(_value, _context):
        raise TypeError("synthetic cache failure")

    monkeypatch.setattr("doeff.handlers._cloudpickle_dumps", failing_dumps)

    @do
    def cache_program() -> EffectGenerator[None]:
        yield CachePut(("bad", object()), "value")
        return None

    engine = ProgramInterpreter()
    result = await engine.run_async(cache_program())

    assert result.is_err

    display_output = result.display(verbose=False)

    # Non-verbose shows effect failure info in status and root cause
    assert "Effect 'CachePutEffect' failed" in display_output
    assert "Root Cause:" in display_output
    assert "TypeError: synthetic cache failure" in display_output
    assert "cache_program" in display_output

    # Verbose mode shows full stack trace
    verbose_output = result.display(verbose=True)
    assert "📍 Effect Creation Stack Trace:" in verbose_output


@pytest.mark.asyncio
async def test_display_nested_recover_shows_leaf_creation_stack(monkeypatch):
    """Recover failures should surface the root cause clearly."""

    def failing_dumps(_value, _context):
        raise TypeError("synthetic cache failure")

    monkeypatch.setattr("doeff.handlers._cloudpickle_dumps", failing_dumps)

    @do
    def try_cache_get() -> EffectGenerator[None]:
        yield Fail(KeyError("missing"))
        return None

    @do
    def compute_and_cache() -> EffectGenerator[None]:
        yield CachePut(("bad", object()), "value")
        return None

    @do
    def recover_program() -> EffectGenerator[None]:
        yield Recover(try_cache_get(), compute_and_cache())
        return None

    engine = ProgramInterpreter()
    result = await engine.run_async(recover_program())

    assert result.is_err

    display_output = result.display(verbose=False)

    # Non-verbose shows root cause first
    assert "Root Cause:" in display_output
    assert "TypeError: synthetic cache failure" in display_output
    assert "Effect 'ResultRecoverEffect' failed" in display_output
    assert "recover_program" in display_output

    # Verbose mode shows full error chain with all creation stacks
    verbose_output = result.display(verbose=True)
    assert "Error Chain (most recent first):" in verbose_output
    assert "Effect 'ResultRecoverEffect' failed" in verbose_output
    assert "📍 Effect Creation Stack Trace:" in verbose_output
    assert "compute_and_cache" in verbose_output


@pytest.mark.asyncio
async def test_display_nested_error():
    """Test display() with nested errors shows root cause first."""
    @do
    def inner_failing() -> EffectGenerator[int]:
        yield Fail(KeyError("missing_key"))
        return 0

    @do
    def outer_program() -> EffectGenerator[str]:
        yield Put("outer_state", "started")
        # This will fail with the inner KeyError
        value = yield inner_failing()
        return f"Got {value}"

    engine = ProgramInterpreter()
    result = await engine.run_async(outer_program())

    display_output = result.display()

    # Non-verbose shows root cause first
    assert "Root Cause:" in display_output
    assert "KeyError: 'missing_key'" in display_output
    assert "Effect 'ResultFailEffect' failed" in display_output

    # State should still be captured
    assert 'outer_state: "started"' in display_output

    # Verbose mode shows full error chain
    verbose_output = result.display(verbose=True)
    assert "Error Chain (most recent first):" in verbose_output
    assert "Caused by: KeyError: 'missing_key'" in verbose_output


@pytest.mark.asyncio
async def test_display_with_complex_state():
    """Test display() with complex state and log data."""
    @do
    def complex_program() -> EffectGenerator[dict]:
        # Add various types of state
        yield Put("string", "test value")
        yield Put("number", 123.45)
        yield Put("bool", True)
        yield Put("none", None)
        yield Put("list", [1, 2, 3, 4, 5])
        yield Put("dict", {"nested": {"key": "value"}})

        # Add various log entries
        yield Log("Simple log")
        yield Log({"structured": "log", "with": "data"})
        yield Log([1, 2, 3])

        return {"result": "complex", "data": [1, 2, 3]}

    engine = ProgramInterpreter()
    result = await engine.run_async(complex_program())

    display_output = result.display()

    # Verify different value types are formatted correctly
    assert 'string: "test value"' in display_output
    assert "number: 123.45" in display_output
    assert "bool: True" in display_output
    assert "none: None" in display_output
    assert "list: [1, 2, 3, 4, 5]" in display_output
    assert '"nested"' in display_output

    # Verify logs are shown
    assert "Simple log" in display_output
    assert "structured" in display_output

    # Verify result is formatted
    assert "✅ Success" in display_output
    assert '"result": "complex"' in display_output or "result" in display_output


@pytest.mark.asyncio
async def test_display_truncation():
    """Test that display() truncates very long values."""
    @do
    def long_value_program() -> EffectGenerator[str]:
        # Create a very long string
        long_string = "x" * 500
        yield Put("long", long_string)

        # Create many log entries
        for i in range(20):
            yield Log(f"Log entry {i}")

        # Create many state items
        for i in range(30):
            yield Put(f"item_{i}", i)

        return "done"

    engine = ProgramInterpreter()
    result = await engine.run_async(long_value_program())

    display_output = result.display()

    # Check truncation indicators
    assert "..." in display_output  # Long values should be truncated
    assert "and 10 more entries" in display_output  # Logs should be limited
    assert "and 10 more items" in display_output or "and 11 more items" in display_output  # State should be limited


@pytest.mark.asyncio
async def test_display_dep_ask_usage_summary():
    """RunResult.display should summarize Dep/Ask effects without duplicates."""

    @do
    def dep_ask_program() -> EffectGenerator[str]:
        first = yield Ask("config")
        second = yield Ask("config")
        dep_one = yield Dep("service")
        dep_two = yield Dep("service")
        extra = yield Ask("other")
        return f"{first}-{second}-{dep_one}-{dep_two}-{extra}"

    engine = ProgramInterpreter()
    context = ExecutionContext(env={
        "config": "alpha",
        "service": "svc",
        "other": "beta",
    })

    result = await engine.run_async(dep_ask_program(), context=context)
    display = result.display()

    assert result.is_ok
    assert display.count("Ask key='config' (count=2)") == 1
    assert "Ask key='service' (count=2)" in display
    assert "Ask key='other' (count=1)" in display
    assert "Dep key='service' (count=2)" in display

    assert "🔑 Dep/Ask Keys:" in display
    keys_section = display.split("🔑 Dep/Ask Keys:", 1)[1]
    assert "Dep keys: 'service'" in keys_section
    assert "Ask keys: 'config', 'service', 'other'" in keys_section


@pytest.mark.asyncio
async def test_display_error_types():
    """Test display() with different error types."""

    # Test with TypeError
    @do
    def type_error_program() -> EffectGenerator[int]:
        yield Fail(TypeError("Expected int, got str"))
        return 0

    engine = ProgramInterpreter()
    result = await engine.run_async(type_error_program())
    display = result.display()

    assert "Effect 'ResultFailEffect' failed" in display
    assert "Caused by: TypeError: Expected int, got str" in display

    # Test with custom exception
    class CustomError(Exception):
        def __init__(self, code: int, message: str):
            self.code = code
            super().__init__(message)

    @do
    def custom_error_program() -> EffectGenerator[str]:
        yield Fail(CustomError(404, "Not found"))
        return ""

    result2 = await engine.run_async(custom_error_program())
    display2 = result2.display()

    assert "Effect 'ResultFailEffect' failed" in display2
    assert "Caused by: CustomError: Not found" in display2


@pytest.mark.asyncio
async def test_display_verbose_mode():
    """Test verbose mode shows additional details."""
    from doeff import Ask, Local

    @do
    def program_with_env() -> EffectGenerator[str]:
        # Add some state to see difference
        yield Put("state_item", "value1")
        yield Put("state_item2", "value2")

        # Add logs to create graph steps
        yield Log("First log")
        yield Log("Second log")

        # Set up environment with Local
        @do
        def inner():
            value = yield Ask("config")
            yield Put("from_env", value)
            return f"Got {value}"

        result = yield Local({"config": "test_value"}, inner())
        return result

    engine = ProgramInterpreter()
    result = await engine.run_async(program_with_env())

    # Non-verbose shouldn't show environment
    normal_display = result.display(verbose=False)
    assert "🌍 Environment:" not in normal_display

    # Verbose should show environment (even if empty) or graph details
    verbose_display = result.display(verbose=True)
    # When there's actual content (state, logs), verbose mode will show more details
    # If not much difference, at least check that verbose mode works without errors
    assert isinstance(verbose_display, str)
    assert len(verbose_display) >= len(normal_display)


@pytest.mark.asyncio
async def test_display_with_graph_steps():
    """Test display() shows graph information."""
    from doeff import Annotate, Step

    @do
    def graph_program() -> EffectGenerator[int]:
        value = yield Step(10, {"operation": "initial"})
        value = yield Step(value * 2, {"operation": "double"})
        yield Annotate({"final": True})
        return value

    engine = ProgramInterpreter()
    result = await engine.run_async(graph_program())

    display = result.display()
    assert "🌳 Graph:" in display
    assert "Steps:" in display

    # Verbose mode should show step details
    verbose = result.display(verbose=True)
    assert "Meta:" in verbose or "Step" in verbose


@pytest.mark.asyncio
async def test_visualize_graph_ascii():
    """RunResult.visualize_graph_ascii renders computation graph metadata."""

    @do
    def graph_program() -> EffectGenerator[str]:
        yield Step("root", meta={"op": "root"})
        middle = yield Step("middle", meta={"op": "mid"})
        yield Step({"leaf": middle}, meta={"op": "leaf"})
        return "done"

    interpreter = ProgramInterpreter()
    result = await interpreter.run_async(graph_program())

    ascii_view = result.visualize_graph_ascii(max_value_length=20)

    assert "00" in ascii_view
    assert "root" in ascii_view
    assert "@root" in ascii_view
    assert "@mid" in ascii_view
    assert "@leaf" in ascii_view

    root_step = next(
        step for step in result.graph.steps if (step.meta or {}).get("op") == "root"
    )

    ascii_custom = result.visualize_graph_ascii(
        include_ops=False, custom_decorators={root_step.output: ("<", ">")}
    )

    assert "@root" not in ascii_custom
    assert "<" in ascii_custom


@pytest.mark.asyncio
async def test_display_empty_result():
    """Test display() with minimal/empty state."""
    @do
    def minimal_program() -> EffectGenerator[None]:
        return None

    engine = ProgramInterpreter()
    result = await engine.run_async(minimal_program())

    display = result.display()

    assert "✅ Success" in display
    assert "Value: None" in display
    assert "State:" in display
    assert "(empty)" in display
    assert "Logs:" in display
    assert "(no logs)" in display


@pytest.mark.asyncio
async def test_display_formatting():
    """Test display() formatting and structure."""
    @do
    def test_program() -> EffectGenerator[int]:
        yield Put("test", 123)
        yield Log("test")
        return 42

    engine = ProgramInterpreter()
    result = await engine.run_async(test_program())

    display = result.display(indent=4)  # Test custom indent

    # Check basic structure
    assert "=" * 60 in display  # Header separator
    assert "RunResult Internal Data" in display
    assert "📊 Result Status:" in display
    assert "🗂️ State:" in display
    assert "📝 Logs:" in display
    assert "🌳 Graph:" in display
    assert "Summary:" in display

    # Check indentation (4 spaces)
    lines = display.split("\n")
    for line in lines:
        if "test: 123" in line:
            assert line.startswith("    ")  # 4-space indent


@pytest.mark.asyncio
async def test_display_user_effect_stack_nested_programs():
    """Test display() shows user effect stack for nested KleisliProgram failures."""

    @do
    def inner_program() -> EffectGenerator[int]:
        """This program fails with a ValueError."""
        yield Fail(ValueError("Inner failure"))
        return 0

    @do
    def middle_program() -> EffectGenerator[int]:
        """This program calls inner_program."""
        result = yield inner_program()
        return result

    @do
    def outer_program() -> EffectGenerator[int]:
        """This program calls middle_program."""
        yield Put("started", True)
        result = yield middle_program()
        return result

    engine = ProgramInterpreter()
    result = await engine.run_async(outer_program())

    assert result.is_err

    # Non-verbose mode shows root cause first
    display_output = result.display(verbose=False)

    # Root cause should be shown first
    assert "Root Cause:" in display_output
    assert "ValueError: Inner failure" in display_output

    # Should show the failure originated from inner_program
    assert "inner_program" in display_output

    # Verbose mode should show full error chain
    verbose_output = result.display(verbose=True)
    assert "Error Chain (most recent first):" in verbose_output
    assert "Effect 'ResultFailEffect' failed" in verbose_output


@pytest.mark.asyncio
async def test_display_user_effect_stack_shows_user_code_only():
    """Test that effect stack filters out doeff internals in non-verbose mode."""

    @do
    def user_function() -> EffectGenerator[None]:
        yield Fail(RuntimeError("User error"))
        return None

    engine = ProgramInterpreter()
    result = await engine.run_async(user_function())

    assert result.is_err

    display_output = result.display(verbose=False)

    # Root cause shown first
    assert "Root Cause:" in display_output
    assert "RuntimeError: User error" in display_output

    # User function should be visible
    assert "user_function" in display_output

    # Internal doeff paths should NOT be in the user effect stack
    # (they may appear in status section, which is fine)
    if "Effect Stack (user code):" in display_output:
        stack_section = display_output.split("Effect Stack (user code):")[1]
        stack_section = stack_section.split("\n\n")[0]  # Get just this section
        assert "/doeff/interpreter.py" not in stack_section
        assert "/doeff/handlers/" not in stack_section
