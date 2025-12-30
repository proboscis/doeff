"""
Comprehensive tests for all effects using @do decorator.

Tests each effect category with both the organized Effects class API
and the capitalized aliases.
"""
# pinjected-linter: ignore  # Testing free monad effects, not using pinjected DI

import asyncio

import pytest

from doeff import (
    IO,
    Annotate,
    # Capitalized aliases
    Ask,
    Await,
    Catch,
    Dep,
    EffectGenerator,
    ExecutionContext,
    Fail,
    Gather,
    Get,
    Listen,
    Local,
    Log,
    Modify,
    Print,
    ProgramInterpreter,
    Put,
    Step,
    Tell,
    annotate,
    # Backwards compatibility
    ask,
    await_,
    catch,
    do,
    get,
    io,
    listen,
    modify,
    print_,
    put,
    step,
    tell,
)

# ============================================
# Reader Effects Tests
# ============================================


@pytest.mark.asyncio
async def test_reader_ask_effect():  # noqa: PINJ040
    """Test Ask effect for reading from environment."""

    @do
    def program() -> EffectGenerator[str]:
        # Test with Effects API
        api_key = yield Ask("api_key")

        # Test with capitalized alias
        db_url = yield Ask("database_url")

        # Test with Dep alias (for pinjected compatibility)
        config = yield Dep("config")

        # Test with lowercase (backwards compatibility)
        secret = yield ask("secret")

        return f"{api_key}|{db_url}|{config}|{secret}"

    engine = ProgramInterpreter()
    context = ExecutionContext(
        env={
            "api_key": "key123",
            "database_url": "postgres://localhost",
            "config": "debug",
            "secret": "shh",
        }
    )

    result = await engine.run_async(program(), context)
    assert result.is_ok
    assert result.value == "key123|postgres://localhost|debug|shh"


@pytest.mark.asyncio
async def test_reader_local_effect():  # noqa: PINJ040
    """Test Local effect for scoped environment changes."""

    @do
    def inner_program() -> EffectGenerator[str]:
        # Should see modified environment
        mode = yield Ask("mode")
        debug = yield Ask("debug")
        return f"inner: mode={mode}, debug={debug}"

    @do
    def outer_program() -> EffectGenerator[tuple]:
        # Original environment
        original_mode = yield Ask("mode")

        # Run with modified environment using Effects API
        inner1 = yield Local(
            {"mode": "test", "debug": True}, inner_program()
        )

        # Run with modified environment using capitalized alias
        inner2 = yield Local(
            {"mode": "prod", "debug": False}, inner_program()
        )

        # Check original is unchanged
        final_mode = yield Ask("mode")

        return (original_mode, inner1, inner2, final_mode)

    engine = ProgramInterpreter()
    context = ExecutionContext(env={"mode": "dev", "debug": True})

    result = await engine.run_async(outer_program(), context)
    assert result.is_ok
    orig, inner1, inner2, final = result.value
    assert orig == "dev"
    assert inner1 == "inner: mode=test, debug=True"
    assert inner2 == "inner: mode=prod, debug=False"
    assert final == "dev"  # Original unchanged


# ============================================
# State Effects Tests
# ============================================


@pytest.mark.asyncio
async def test_state_get_effect():  # noqa: PINJ040
    """Test Get effect for reading state."""

    @do
    def program() -> EffectGenerator[tuple]:
        # Test with Effects API
        count1 = yield Get("counter")

        # Test with capitalized alias
        count2 = yield Get("counter")

        # Test with lowercase (backwards compatibility)
        count3 = yield get("counter")

        # Non-existent key returns None
        missing = yield Get("missing")

        return (count1, count2, count3, missing)

    engine = ProgramInterpreter()
    context = ExecutionContext(state={"counter": 42})

    result = await engine.run_async(program(), context)
    assert result.is_ok
    assert result.value == (42, 42, 42, None)


@pytest.mark.asyncio
async def test_state_put_effect():  # noqa: PINJ040
    """Test Put effect for updating state."""

    @do
    def program() -> EffectGenerator[dict]:
        # Test with Effects API
        yield Put("name", "Alice")

        # Test with capitalized alias
        yield Put("age", 30)

        # Test with lowercase (backwards compatibility)
        yield put("city", "NYC")

        # Get all values
        name = yield Get("name")
        age = yield Get("age")
        city = yield Get("city")

        return {"name": name, "age": age, "city": city}

    engine = ProgramInterpreter()
    context = ExecutionContext()

    result = await engine.run_async(program(), context)
    assert result.is_ok
    assert result.value == {"name": "Alice", "age": 30, "city": "NYC"}
    assert result.state == {"name": "Alice", "age": 30, "city": "NYC"}


@pytest.mark.asyncio
async def test_state_modify_effect():  # noqa: PINJ040
    """Test Modify effect for transforming state values."""

    @do
    def program() -> EffectGenerator[tuple]:
        # Initial state
        yield Put("counter", 10)
        yield Put("names", ["Alice"])

        # Test with Effects API
        new_counter = yield Modify("counter", lambda x: x * 2)

        # Test with capitalized alias
        new_names = yield Modify("names", lambda xs: [*xs, "Bob"])

        # Test with lowercase (backwards compatibility)
        final_counter = yield modify("counter", lambda x: x + 5)

        return (new_counter, new_names, final_counter)

    engine = ProgramInterpreter()
    context = ExecutionContext()

    result = await engine.run_async(program(), context)
    assert result.is_ok
    assert result.value == (20, ["Alice", "Bob"], 25)
    assert result.state["counter"] == 25
    assert result.state["names"] == ["Alice", "Bob"]


# ============================================
# Writer Effects Tests
# ============================================


@pytest.mark.asyncio
async def test_writer_tell_effect():  # noqa: PINJ040
    """Test Tell/Log effect for logging."""

    @do
    def program() -> EffectGenerator[None]:
        # Test with Effects API
        yield Tell("Starting process")

        # Test with capitalized aliases
        yield Log("Processing data")
        yield Tell({"event": "checkpoint", "progress": 50})

        # Test with lowercase (backwards compatibility)
        yield tell("Finishing up")

    engine = ProgramInterpreter()
    context = ExecutionContext()

    result = await engine.run_async(program(), context)
    assert result.is_ok
    assert result.log == [
        "Starting process",
        "Processing data",
        {"event": "checkpoint", "progress": 50},
        "Finishing up",
    ]


@pytest.mark.asyncio
async def test_writer_log_limit_trims_entries():
    """ProgramInterpreter trims the writer log when a limit is set."""

    @do
    def program() -> EffectGenerator[None]:
        for index in range(5):
            yield Tell(f"event-{index}")

    engine = ProgramInterpreter(max_log_entries=3)
    result = await engine.run_async(program(), ExecutionContext())

    assert result.is_ok
    assert list(result.log) == ["event-2", "event-3", "event-4"]


@pytest.mark.asyncio
async def test_writer_listen_effect():  # noqa: PINJ040
    """Test Listen effect for capturing sub-computation logs."""

    @do
    def sub_program() -> EffectGenerator[int]:
        yield Log("Sub: step 1")
        yield Log("Sub: step 2")
        yield Log("Sub: step 3")
        return 42

    @do
    def main_program() -> EffectGenerator[tuple]:
        yield Log("Main: starting")

        # Test with Effects API
        value1, log1 = yield Listen(sub_program())

        # Test with capitalized alias
        value2, log2 = yield Listen(sub_program())

        # Test with lowercase (backwards compatibility)
        value3, log3 = yield listen(sub_program())

        yield Log("Main: done")

        return (value1, len(log1), value2, len(log2), value3, len(log3))

    engine = ProgramInterpreter()
    context = ExecutionContext()

    result = await engine.run_async(main_program(), context)
    assert result.is_ok
    assert result.value == (42, 3, 42, 3, 42, 3)
    assert result.log == ["Main: starting", "Main: done"]


@pytest.mark.asyncio
async def test_writer_listen_respects_log_limit():
    """listen() should surface only the latest entries when a limit is configured."""

    @do
    def noisy_sub_program() -> EffectGenerator[int]:
        for index in range(5):
            yield Tell(f"sub-{index}")
        return 7

    @do
    def main() -> EffectGenerator[list[str]]:
        result, captured = yield Listen(noisy_sub_program())
        assert result == 7
        return list(captured)

    engine = ProgramInterpreter(max_log_entries=2)
    run_result = await engine.run_async(main(), ExecutionContext())

    assert run_result.is_ok
    assert run_result.value == ["sub-3", "sub-4"]


# ============================================
# Future Effects Tests
# ============================================


@pytest.mark.asyncio
async def test_future_await_effect():  # noqa: PINJ040
    """Test Await effect for async operations."""

    async def async_fetch(value: str) -> str:
        await asyncio.sleep(0.01)
        return f"fetched: {value}"

    @do
    def program() -> EffectGenerator[tuple]:
        # Test with Effects API
        result1 = yield Await(async_fetch("data1"))

        # Test with capitalized alias
        result2 = yield Await(async_fetch("data2"))

        # Test with lowercase (backwards compatibility)
        result3 = yield await_(async_fetch("data3"))

        return (result1, result2, result3)

    engine = ProgramInterpreter()
    context = ExecutionContext()

    result = await engine.run_async(program(), context)
    assert result.is_ok
    assert result.value == ("fetched: data1", "fetched: data2", "fetched: data3")


@pytest.mark.asyncio
async def test_future_parallel_effect():  # noqa: PINJ040
    """Test Gather effect for concurrent async operations.

    Gather works with Programs to provide concurrent execution.
    """

    async def async_process(n: int) -> int:
        await asyncio.sleep(0.01)
        return n * 2

    @do
    def make_worker(n: int) -> EffectGenerator[int]:
        """Wrap async operation in a Program."""
        result = yield Await(async_process(n))
        return result

    @do
    def program() -> EffectGenerator[tuple]:
        # Test with Gather (concurrent execution of Programs)
        results1 = yield Gather(
            make_worker(1), make_worker(2), make_worker(3)
        )

        # Test another batch
        results2 = yield Gather(make_worker(4), make_worker(5))

        # Test third batch
        results3 = yield Gather(make_worker(6), make_worker(7), make_worker(8))

        return (results1, results2, results3)

    engine = ProgramInterpreter()
    context = ExecutionContext()

    result = await engine.run_async(program(), context)
    assert result.is_ok
    assert result.value == ([2, 4, 6], [8, 10], [12, 14, 16])


# ============================================
# Result Effects Tests
# ============================================


@pytest.mark.asyncio
async def test_result_fail_effect():  # noqa: PINJ040
    """Test Fail effect for error signaling."""

    @do
    def program() -> EffectGenerator[str]:
        # Test with Effects API
        yield Fail(ValueError("Effects API error"))
        return "should not reach"

    engine = ProgramInterpreter()
    context = ExecutionContext()

    result = await engine.run_async(program(), context)
    assert result.is_err
    # Unwrap EffectFailure if needed
    error = result.result.error
    from doeff.types import EffectFailure
    if isinstance(error, EffectFailure):
        error = error.cause
    assert "Effects API error" in str(error)

    @do
    def program2() -> EffectGenerator[str]:
        # Test with capitalized alias
        yield Fail(RuntimeError("Capitalized error"))
        return "should not reach"

    result2 = await engine.run_async(program2(), context)
    assert result2.is_err
    # Unwrap EffectFailure if needed
    error2 = result2.result.error
    from doeff.types import EffectFailure
    if isinstance(error2, EffectFailure):
        error2 = error2.cause
    assert "Capitalized error" in str(error2)


@pytest.mark.asyncio
async def test_result_catch_effect():  # noqa: PINJ040
    """Test Catch effect for error handling."""

    @do
    def failing_program() -> EffectGenerator[str]:
        yield Fail(ValueError("intentional failure"))
        return "should not reach"

    @do
    def success_program() -> EffectGenerator[str]:
        yield Log("Success program running")
        return "success"

    @do
    def recovery_handler(error: Exception) -> EffectGenerator[str]:
        yield Log(f"Caught error: {error}")
        return f"recovered from {type(error).__name__}"

    @do
    def main_program() -> EffectGenerator[tuple]:
        # Test with Effects API
        result1 = yield Catch(failing_program(), recovery_handler)

        # Test with capitalized alias
        result2 = yield Catch(failing_program(), recovery_handler)

        # Test with lowercase (backwards compatibility)
        result3 = yield catch(failing_program(), recovery_handler)

        # Test successful case - should not trigger recovery
        result4 = yield Catch(success_program(), recovery_handler)

        return (result1, result2, result3, result4)

    engine = ProgramInterpreter()
    context = ExecutionContext()

    result = await engine.run_async(main_program(), context)
    assert result.is_ok
    assert result.value == (
        "recovered from ValueError",
        "recovered from ValueError",
        "recovered from ValueError",
        "success",
    )
    assert len(result.log) == 4  # Three recoveries + one success log


# ============================================
# IO Effects Tests
# ============================================


@pytest.mark.asyncio
async def test_io_run_effect():  # noqa: PINJ040
    """Test IO run effect for side effects."""

    # Mutable state to track side effects
    side_effects = []

    @do
    def program() -> EffectGenerator[tuple]:
        # Test with Effects API
        result1 = yield IO(
            lambda: side_effects.append("effect1") or "done1"
        )

        # Test with capitalized alias
        result2 = yield IO(lambda: side_effects.append("effect2") or "done2")

        # Test with lowercase (backwards compatibility)
        result3 = yield io(lambda: side_effects.append("effect3") or "done3")

        return (result1, result2, result3)

    engine = ProgramInterpreter()
    context = ExecutionContext(io_allowed=True)

    result = await engine.run_async(program(), context)
    assert result.is_ok
    assert result.value == ("done1", "done2", "done3")
    assert side_effects == ["effect1", "effect2", "effect3"]


@pytest.mark.asyncio
async def test_io_print_effect(capsys):  # noqa: PINJ040
    """Test Print effect for console output."""

    @do
    def program() -> EffectGenerator[None]:
        # Test with Effects API
        yield Print("Message from Effects API")

        # Test with capitalized alias
        yield Print("Message from Print")

        # Test with lowercase (backwards compatibility)
        yield print_("Message from print_")

    engine = ProgramInterpreter()
    context = ExecutionContext(io_allowed=True)

    result = await engine.run_async(program(), context)
    assert result.is_ok

    captured = capsys.readouterr()
    assert "Message from Effects API" in captured.out
    assert "Message from Print" in captured.out
    assert "Message from print_" in captured.out


@pytest.mark.asyncio
async def test_io_not_allowed():  # noqa: PINJ040
    """Test that IO effects fail when io_allowed=False."""

    @do
    def program() -> EffectGenerator[None]:
        yield Print("Should fail")

    engine = ProgramInterpreter()
    context = ExecutionContext(io_allowed=False)

    result = await engine.run_async(program(), context)
    assert result.is_err
    # Unwrap EffectFailure if needed
    error = result.result.error
    from doeff.types import EffectFailure
    if isinstance(error, EffectFailure):
        error = error.cause
    assert "IO not allowed" in str(error)


# ============================================
# Graph Effects Tests
# ============================================


@pytest.mark.asyncio
async def test_graph_step_effect():  # noqa: PINJ040
    """Test Step effect for graph building."""

    @do
    def program() -> EffectGenerator[tuple]:
        # Test with Effects API
        result1 = yield Step("value1", {"type": "api"})

        # Test with capitalized alias
        result2 = yield Step("value2", {"type": "alias"})

        # Test with lowercase (backwards compatibility)
        result3 = yield step("value3", {"type": "compat"})

        # Without metadata
        result4 = yield Step("value4")

        return (result1, result2, result3, result4)

    engine = ProgramInterpreter()
    context = ExecutionContext()

    result = await engine.run_async(program(), context)
    assert result.is_ok
    assert result.value == ("value1", "value2", "value3", "value4")
    assert len(result.graph.steps) == 5  # Includes initial step

    # Check metadata is preserved
    steps = list(result.graph.steps)
    # FrozenDict items is a method, not an attribute
    assert any(
        s.meta.items() and any(k == "type" and v == "api" for k, v in s.meta.items())
        for s in steps
    )
    assert any(
        s.meta.items() and any(k == "type" and v == "alias" for k, v in s.meta.items())
        for s in steps
    )
    assert any(
        s.meta.items() and any(k == "type" and v == "compat" for k, v in s.meta.items())
        for s in steps
    )


@pytest.mark.asyncio
async def test_graph_annotate_effect():  # noqa: PINJ040
    """Test Annotate effect for adding metadata to graph nodes."""

    @do
    def program() -> EffectGenerator[None]:
        # Add initial step
        yield Step("initial")

        # Test with Effects API
        yield Annotate({"stage": "processing", "version": 1})

        # Add another step
        yield Step("middle")

        # Test with capitalized alias
        yield Annotate({"stage": "validation", "version": 2})

        # Add final step
        yield Step("final")

        # Test with lowercase (backwards compatibility)
        yield annotate({"stage": "complete", "version": 3})

    engine = ProgramInterpreter()
    context = ExecutionContext()

    result = await engine.run_async(program(), context)
    assert result.is_ok
    assert len(result.graph.steps) == 4  # Includes initial step

    # The last step should have the final annotation
    last_step = result.graph.last
    # FrozenDict items is a method
    assert any(k == "stage" and v == "complete" for k, v in last_step.meta.items())
    assert any(k == "version" and v == 3 for k, v in last_step.meta.items())


# ============================================
# Integration Tests
# ============================================


@pytest.mark.asyncio
async def test_all_effects_integration():  # noqa: PINJ040
    """Test using all effect types together."""

    @do
    def comprehensive_program() -> EffectGenerator[dict]:
        # Reader
        config = yield Ask("config")

        # State
        yield Put("counter", 0)

        # Writer
        yield Log("Starting comprehensive test")

        # Graph
        yield Step("initialization", {"phase": "start"})

        # Async
        async def fetch() -> str:
            await asyncio.sleep(0.01)
            return "async_data"

        data = yield Await(fetch())

        # Gather (parallel execution of Programs)
        async def process(n: int) -> int:
            return n * 2

        @do
        def process_worker(n: int) -> EffectGenerator[int]:
            result = yield Await(process(n))
            return result

        results = yield Gather(process_worker(1), process_worker(2), process_worker(3))

        # State modification
        final_count = yield Modify("counter", lambda x: x + sum(results))

        # IO
        if config.get("debug"):
            yield Print(f"Debug: counter={final_count}")

        # Error handling
        @do
        def maybe_fail() -> EffectGenerator[str]:
            if config.get("fail"):
                yield Fail(ValueError("Intentional"))
            return "ok"

        def catch_handler(e):
            @do
            def handle() -> EffectGenerator[str]:
                yield Log(f"Caught: {e}")
                return "recovered"
            return handle()

        status = yield Catch(
            maybe_fail(),
            catch_handler,
        )

        # Graph annotation
        yield Annotate({"phase": "complete", "status": status})

        # Listen to sub-computation
        @do
        def sub() -> EffectGenerator[int]:
            yield Log("sub1")
            yield Log("sub2")
            return 99

        sub_value, sub_log = yield Listen(sub())

        return {
            "data": data,
            "results": results,
            "counter": final_count,
            "status": status,
            "sub_value": sub_value,
            "sub_log_count": len(sub_log),
        }

    engine = ProgramInterpreter()

    # Test successful case
    context = ExecutionContext(
        env={"config": {"debug": True, "fail": False}}, io_allowed=True
    )

    result = await engine.run_async(comprehensive_program(), context)
    assert result.is_ok
    assert result.value["data"] == "async_data"
    assert result.value["results"] == [2, 4, 6]
    assert result.value["counter"] == 12
    assert result.value["status"] == "ok"
    assert result.value["sub_value"] == 99
    assert result.value["sub_log_count"] == 2

    # Check logs
    assert "Starting comprehensive test" in result.log

    # Check graph
    assert len(result.graph.steps) > 0
    # FrozenDict items is a method
    assert any(
        k == "phase" and v == "complete" for k, v in result.graph.last.meta.items()
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
