"""Test stack safety of the comprehensive pragmatic approach."""

import asyncio
from collections.abc import Generator
from typing import Any

import pytest

from doeff import (
    Effect,
    ExecutionContext,
    Program,
    ProgramInterpreter,
    annotate,
    ask,
    await_,
    catch,
    do,
    fail,
    get,
    io,
    listen,
    modify,
    parallel,
    put,
    step,
    tell,
)
from doeff.program import KleisliProgramCall


@pytest.mark.asyncio
async def test_deep_mixed_monad_chain():  # noqa: PLR0915
    """Test deep chains using all monad types."""

    def deep_mixed_program() -> Generator[Effect, Any, dict]:
        """Program with 10,000 operations mixing all monad types."""

        # Initialize
        yield put("total", 0)
        yield tell("Starting deep mixed chain")

        # Deep chain with all monad types
        for i in range(5000):  # 5000 iterations, multiple effects each
            # Reader
            multiplier = yield ask("multiplier")

            # State
            current = yield get("total")
            yield put("total", current + multiplier)

            # Writer
            if i % 1000 == 0:
                yield tell(f"Milestone: {i}")

            # Future (lightweight async to avoid slowdown)
            if i % 500 == 0:
                value = yield await_(quick_async(i))

            # Graph
            if i % 100 == 0:
                yield step(current, meta={"iteration": i})
                yield annotate({"progress": i / 5000})

            # IO (occasional)
            if i % 2000 == 0:
                yield io(lambda: None)  # No-op IO

            # Result (error handling)
            if i % 1500 == 0:
                def error_recovery(e, current_i=i):  # Capture i via default argument
                    @do
                    def recover() -> Generator[Effect, Any, int]:
                        yield tell(f"Recovered from error at {current_i}: {e}")
                        return -current_i
                    return recover()

                yield catch(maybe_fail(i), error_recovery)

        # Final results
        final_total = yield get("total")

        @do
        def sub_program() -> Generator[Effect, Any, int]:
            """Sub computation with logging."""
            yield tell("Sub computation")
            yield put("sub_state", 42)
            return 42

        listen_result = yield listen(sub_program())
        # Handle ListenResult object
        if hasattr(listen_result, "value") and hasattr(listen_result, "log"):
            value = listen_result.value
            sub_log = listen_result.log
        else:
            value, sub_log = listen_result

        return {
            "iterations": 5000,
            "final_total": final_total,
            "sub_value": value,
            "sub_log_size": len(sub_log),
        }

    async def quick_async(n):
        """Quick async operation."""
        return n * 2

    @do
    def maybe_fail(n: int) -> Generator[Effect, Any, int]:
        """Sometimes fails."""
        if n == 3000:
            yield fail(ValueError("Expected failure"))
        return n

    @do
    def recover_value(n: int) -> Generator[Effect, Any, int]:
        """Recovery function."""
        yield tell(f"Recovered from error at {n}")
        return -n

    @do
    def sub_computation() -> Generator[Effect, Any, int]:
        """Sub computation with logging."""
        yield tell("Sub computation")
        yield put("sub_state", 42)
        return 42

    # Setup and run
    engine = ProgramInterpreter()
    context = ExecutionContext(env={"multiplier": 2}, io_allowed=True)

    program = KleisliProgramCall.create_anonymous(deep_mixed_program)
    result = await engine.run_async(program, context)

    assert result.is_ok
    assert result.value["iterations"] == 5000
    assert result.value["final_total"] == 10000  # 5000 * 2
    assert len(result.log) > 5  # Should have multiple log entries
    assert len(result.graph.steps) > 50  # Should have many graph steps

    print(f"✅ Deep mixed chain completed: {result.value['iterations']} iterations")
    print(f"   Total operations: ~{result.value['iterations'] * 8} effects handled")
    print(f"   Final state total: {result.value['final_total']}")
    print(f"   Log entries: {len(result.log)}")
    print(f"   Graph steps: {len(result.graph.steps)}")


@pytest.mark.asyncio
async def test_nested_monad_operations():
    """Test deeply nested monad operations."""

    def nested_program(depth: int) -> Generator[Effect, Any, int]:
        """Recursively nested program."""
        if depth == 0:
            if False:  # Make it a generator
                yield
            return 1

        # Each level uses multiple monad types
        yield put(f"depth_{depth}", depth)
        yield tell(f"At depth {depth}")

        # Nested catch for error handling
        @do
        def next_level() -> Generator[Effect, Any, int]:
            return (yield from nested_program(depth - 1))

        def error_handler(e):
            """Error handler that returns a Program."""
            @do
            def handle() -> Generator[Effect, Any, int]:
                yield tell(f"Error at depth {depth}: {e}")
                return 0
            return handle()

        result = yield catch(
            next_level(),
            error_handler,
        )

        # State modification
        yield modify(f"depth_{depth}", lambda x: x * 2)

        # Async operation
        if depth % 10 == 0:
            yield await_(asyncio.sleep(0.001))

        return result + 1

    engine = ProgramInterpreter()
    context = ExecutionContext()

    # Test with depth 100 (should work fine)
    program = KleisliProgramCall.create_anonymous(lambda: nested_program(100))
    result = await engine.run_async(program, context)

    assert result.is_ok
    assert result.value == 101  # 1 + 100
    assert len(result.state) == 100  # One state entry per depth

    print(f"✅ Nested operations completed: depth 100, result={result.value}")


@pytest.mark.asyncio
async def test_parallel_async_operations():
    """Test handling many parallel async operations."""

    def parallel_program() -> Generator[Effect, Any, list[int]]:
        """Program with many parallel operations."""
        results = []

        # Process in batches
        for batch in range(100):  # 100 batches
            yield tell(f"Processing batch {batch}")

            # Parallel operations
            batch_results = yield parallel(
                *[quick_operation(batch * 10 + i) for i in range(10)]
            )
            results.extend(batch_results)

            # Update state
            yield put(f"batch_{batch}", sum(batch_results))

            # Graph tracking
            if batch % 10 == 0:
                yield step(len(results), meta={"batch": batch})

        return results

    async def quick_operation(n: int) -> int:
        """Quick async operation."""
        await asyncio.sleep(0.0001)
        return n

    engine = ProgramInterpreter()
    context = ExecutionContext()

    program = KleisliProgramCall.create_anonymous(parallel_program)
    result = await engine.run_async(program, context)

    assert result.is_ok
    assert len(result.value) == 1000  # 100 batches * 10 items
    assert len(result.state) == 100  # One entry per batch

    print(f"✅ Parallel operations completed: {len(result.value)} items processed")


@pytest.mark.asyncio
async def test_monad_composition_patterns():
    """Test various monad composition patterns."""

    def composition_program() -> Generator[Effect, Any, dict]:
        """Test different composition patterns."""
        results = {}

        # ReaderT over StateT pattern
        yield ask("config")
        yield put("configured", True)

        # WriterT over Future pattern
        yield tell("Starting async operations")
        yield await_(asyncio.sleep(0.001))
        yield tell("Async completed")

        # StateT over Result pattern
        @do
        def stateful_program() -> Generator[Effect, Any, int]:
            yield put("computed", 42)
            value = yield get("computed")
            return value

        @do
        def default_program() -> Generator[Effect, Any, int]:
            yield put("computed", 0)
            return 0

        try_result = yield catch(
            stateful_program(), lambda _e: default_program()
        )

        # Listen + Local pattern (Writer + Reader)
        @do
        def local_program() -> Generator[Effect, Any, str]:
            yield tell("In local computation")
            return "local_result"

        listen_result = yield listen(local_program())
        # Handle ListenResult object
        if hasattr(listen_result, "value") and hasattr(listen_result, "log"):
            value = listen_result.value
            log = listen_result.log
        else:
            value, log = listen_result

        results["try_result"] = try_result
        results["logged_value"] = value
        results["log_size"] = len(log)

        return results


    engine = ProgramInterpreter()
    context = ExecutionContext(env={"config": {"key": "value"}})

    program = KleisliProgramCall.create_anonymous(composition_program)
    result = await engine.run_async(program, context)

    assert result.is_ok
    assert result.value["try_result"] == 42
    assert result.value["logged_value"] == "local_result"
    assert result.value["log_size"] == 1

    print("✅ Monad composition patterns work correctly")


if __name__ == "__main__":
    # Run tests
    asyncio.run(test_deep_mixed_monad_chain())
    asyncio.run(test_nested_monad_operations())
    asyncio.run(test_parallel_async_operations())
    asyncio.run(test_monad_composition_patterns())
    print("\n🎉 All comprehensive stack safety tests passed!")
