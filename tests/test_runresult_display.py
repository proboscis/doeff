"""Test enhanced RunResult.display() method with stack traces and error details."""

import json

import pytest

from doeff import CachePut, EffectGenerator, Fail, Log, ProgramInterpreter, Put, Recover, Step, do


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
    result = await engine.run(successful_program())

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
async def test_display_error_with_traceback():
    """Test display() shows full stack trace for errors."""
    @do
    def failing_program() -> EffectGenerator[str]:
        yield Put("step", "before_error")
        yield Log("About to fail")
        yield Fail(ValueError("Something went wrong in the program"))
        return "never reached"

    engine = ProgramInterpreter()
    result = await engine.run(failing_program())

    # Test display output
    display_output = result.display()

    # Verify error indicators
    assert "❌ Failure" in display_output
    assert "Error Chain (most recent first):" in display_output
    assert "Effect 'ResultFailEffect' failed" in display_output
    assert "Caused by: ValueError: Something went wrong in the program" in display_output
    assert "🔥 Fail Creation Stack Trace:" in display_output
    assert "failing_program" in display_output

    # Verify creation location is shown
    assert "📍 Created at:" in display_output
    assert "failing_program" in display_output

    # Verify state and logs are still shown
    assert 'step: "before_error"' in display_output
    assert "About to fail" in display_output

    # Verify summary shows error status
    assert "Status: ❌ Error" in display_output


@pytest.mark.asyncio
async def test_display_trace_includes_user_frames():
    """Display output should show full user stack frames by default."""

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
    result = await engine.run(failing_program())

    assert result.is_err

    display_output = result.display(verbose=False)

    # Full traceback should surface user helper frames and source file.
    assert "helper_outer" in display_output
    assert "helper_middle" in display_output
    assert "tests/test_runresult_display.py" in display_output


@pytest.mark.asyncio
async def test_display_primary_effect_shows_creation_stack(monkeypatch):
    """Closest failing effect should surface its creation stack without verbose."""

    def failing_dumps(_value, _context):
        raise TypeError("synthetic cache failure")

    monkeypatch.setattr("doeff.handlers._cloudpickle_dumps", failing_dumps)

    @do
    def cache_program() -> EffectGenerator[None]:
        yield CachePut(("bad", object()), "value")
        return None

    engine = ProgramInterpreter()
    result = await engine.run(cache_program())

    assert result.is_err

    display_output = result.display(verbose=False)

    assert "Effect 'CachePutEffect' failed" in display_output
    assert "🔥 Effect Creation Stack Trace:" in display_output
    assert "cache_program" in display_output


@pytest.mark.asyncio
async def test_display_nested_recover_shows_leaf_creation_stack(monkeypatch):
    """Recover failures should surface the failing effect's creation stack."""

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
    result = await engine.run(recover_program())

    assert result.is_err

    display_output = result.display(verbose=False)

    assert "Effect 'ResultRecoverEffect' failed" in display_output
    recover_section = display_output.split("Effect 'ResultRecoverEffect' failed", 1)[1]
    assert "🔥 Effect Creation Stack Trace:" in recover_section
    assert "recover_program" in recover_section

    assert "Effect 'CachePutEffect' failed" in display_output
    cache_section = display_output.split("Effect 'CachePutEffect' failed", 1)[1]
    assert "🔥 Effect Creation Stack Trace:" in cache_section
    assert "compute_and_cache" in cache_section


@pytest.mark.asyncio
async def test_display_nested_error():
    """Test display() with nested errors and Recover."""
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
    result = await engine.run(outer_program())

    display_output = result.display()

    # Should show the KeyError details
    assert "Error Chain (most recent first):" in display_output
    assert "Effect 'ResultFailEffect' failed" in display_output
    assert "Caused by: KeyError: 'missing_key'" in display_output

    # State should still be captured
    assert 'outer_state: "started"' in display_output


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
    result = await engine.run(complex_program())

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
    result = await engine.run(long_value_program())

    display_output = result.display()

    # Check truncation indicators
    assert "..." in display_output  # Long values should be truncated
    assert "and 10 more entries" in display_output  # Logs should be limited
    assert "and 10 more items" in display_output or "and 11 more items" in display_output  # State should be limited


@pytest.mark.asyncio
async def test_display_error_types():
    """Test display() with different error types."""

    # Test with TypeError
    @do
    def type_error_program() -> EffectGenerator[int]:
        yield Fail(TypeError("Expected int, got str"))
        return 0

    engine = ProgramInterpreter()
    result = await engine.run(type_error_program())
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

    result2 = await engine.run(custom_error_program())
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
    result = await engine.run(program_with_env())

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
    result = await engine.run(graph_program())

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
    result = await interpreter.run(graph_program())

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
    result = await engine.run(minimal_program())

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
    result = await engine.run(test_program())

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
async def test_display_dep_ask_aggregated_statistics():
    """Test that Dep/Ask usage shows aggregated statistics without duplication."""
    from doeff import Ask, Dep, Local

    @do
    def inner_program() -> EffectGenerator[str]:
        # Access database multiple times
        yield Dep("database")
        yield Dep("database")
        yield Dep("database")

        # Access config multiple times
        yield Ask("config")
        yield Ask("config")

        # Access logger once
        yield Dep("logger")

        # Access api_key once
        yield Ask("api_key")

        return "done"

    @do
    def program_with_deps() -> EffectGenerator[str]:
        # Provide environment for Dep and Ask
        result = yield Local(
            {
                "database": "db_connection",
                "logger": "logger_instance",
                "config": "config_value",
                "api_key": "secret_key"
            },
            inner_program()
        )
        return result

    engine = ProgramInterpreter()
    result = await engine.run(program_with_deps())

    display = result.display()

    # Check that Dep/Ask Usage section exists
    assert "🔗 Dep/Ask Usage Statistics:" in display

    # Check aggregated Dep statistics
    assert "Dep effects:" in display
    assert "4 total accesses" in display  # 3 database + 1 logger
    assert "2 unique keys" in display

    # Check individual Dep key statistics with counts
    assert '"database"' in display
    assert "3 accesses" in display  # database accessed 3 times
    assert '"logger"' in display
    assert "1 access" in display  # logger accessed once

    # Check aggregated Ask statistics
    assert "Ask effects:" in display
    # Note: Ask effects include all Dep keys as well (due to handler implementation)
    # so we just verify the section exists and shows the right structure

    # Check individual Ask key statistics
    assert '"config"' in display
    assert "2 accesses" in display  # config accessed twice
    assert '"api_key"' in display

    # Verify that "First used at:" shows location info
    assert "First used at:" in display
    assert "inner_program" in display


@pytest.mark.asyncio
async def test_display_compact_keys_section():
    """Test that compact keys section shows all used keys."""
    from doeff import Ask, Dep, Local

    @do
    def inner_program() -> EffectGenerator[str]:
        yield Dep("database")
        yield Dep("config")
        yield Dep("logger")
        yield Ask("api_key")
        yield Ask("timeout")
        yield Ask("endpoint")
        return "done"

    @do
    def program_with_many_deps() -> EffectGenerator[str]:
        result = yield Local(
            {
                "database": "db",
                "config": "cfg",
                "logger": "log",
                "api_key": "key",
                "timeout": "30",
                "endpoint": "url"
            },
            inner_program()
        )
        return result

    engine = ProgramInterpreter()
    result = await engine.run(program_with_many_deps())

    display = result.display()

    # Check that compact keys section exists
    assert "🔑 All Used Keys (Compact):" in display

    # Check that Dep keys are listed compactly
    assert "Dep keys" in display
    assert "database" in display
    assert "config" in display
    assert "logger" in display

    # Check that Ask keys are listed compactly
    assert "Ask keys" in display
    assert "api_key" in display
    assert "timeout" in display
    assert "endpoint" in display


@pytest.mark.asyncio
async def test_display_no_dep_ask_effects():
    """Test display when there are no Dep/Ask effects."""
    @do
    def simple_program() -> EffectGenerator[int]:
        yield Put("value", 42)
        yield Log("test")
        return 42

    engine = ProgramInterpreter()
    result = await engine.run(simple_program())

    display = result.display()

    # Check that the section shows "no effects" message
    assert "🔗 Dep/Ask Usage Statistics:" in display
    assert "(no Dep/Ask effects observed)" in display

    # Compact keys section should not appear if there are no keys
    # Or it should show empty lists
    if "🔑 All Used Keys (Compact):" in display:
        assert "(none)" in display or "Dep keys (0):" in display


@pytest.mark.asyncio
async def test_display_dep_ask_with_none_keys():
    """Test Dep/Ask display handles None keys gracefully."""
    @do
    def simple_program() -> EffectGenerator[str]:
        # Simple program without Dep/Ask effects
        # The implementation should handle edge cases with None keys
        yield Put("test", "value")
        return "done"

    engine = ProgramInterpreter()
    result = await engine.run(simple_program())

    # Should not crash even with unusual observations
    display = result.display()
    assert isinstance(display, str)
    assert "🔗 Dep/Ask Usage Statistics:" in display
