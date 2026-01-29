"""Tests for effect combination behaviors.

This test module validates the composition laws defined in SPEC-EFF-100-combinations.md.
It tests how effects interact when nested or composed, ensuring consistent and
predictable behavior.

Reference: gh#180, specs/effects/SPEC-EFF-100-combinations.md
"""

import pytest

from doeff import Program, do
from doeff.cesk.runtime.async_ import AsyncRuntime
from doeff.cesk.runtime.sync import SyncRuntime
from doeff.effects import (
    Ask,
    Delay,
    Gather,
    Get,
    GetTime,
    Listen,
    Local,
    Modify,
    Put,
    Safe,
    Tell,
)
from doeff.effects.reader import AskEffect

# ============================================================================
# Law 1: Local Restoration Law Tests
# ============================================================================


class TestLocalRestorationLaw:
    """Tests for Law 1: Environment MUST restore after Local scope."""

    @pytest.mark.asyncio
    async def test_local_restores_env_on_success(self) -> None:
        """Law 1: Environment MUST restore after Local scope, regardless of success."""
        runtime = AsyncRuntime()

        @do
        def inner_program():
            inner_val = yield Ask("key")
            return inner_val

        @do
        def program():
            outer_before = yield Ask("key")
            inner_result = yield Local({"key": "inner_value"}, inner_program())
            outer_after = yield Ask("key")
            return (outer_before, inner_result, outer_after)

        result = await runtime.run_and_unwrap(program(), env={"key": "outer_value"})
        outer_before, inner_result, outer_after = result

        assert outer_before == "outer_value"
        assert inner_result == "inner_value"
        assert outer_after == "outer_value"

    @pytest.mark.asyncio
    async def test_local_restores_env_on_error(self) -> None:
        """Law 1: Environment MUST restore after Local scope, regardless of error."""
        runtime = AsyncRuntime()

        @do
        def failing_inner():
            yield Ask("key")
            raise ValueError("inner error")

        @do
        def program():
            outer_before = yield Ask("key")
            result = yield Safe(Local({"key": "inner_value"}, failing_inner()))
            outer_after = yield Ask("key")
            return (outer_before, result.is_err(), outer_after)

        result = await runtime.run_and_unwrap(program(), env={"key": "outer_value"})
        outer_before, had_error, outer_after = result

        assert outer_before == "outer_value"
        assert had_error is True
        assert outer_after == "outer_value"

    @pytest.mark.asyncio
    async def test_nested_local_override_and_restore(self) -> None:
        """Law 1: Each Local scope independently restores its environment."""
        runtime = AsyncRuntime()

        @do
        def level3():
            return (yield Ask("key"))

        @do
        def level2():
            before = yield Ask("key")
            inner = yield Local({"key": "level3"}, level3())
            after = yield Ask("key")
            return (before, inner, after)

        @do
        def level1():
            before = yield Ask("key")
            inner = yield Local({"key": "level2"}, level2())
            after = yield Ask("key")
            return (before, inner, after)

        result = await runtime.run_and_unwrap(level1(), env={"key": "level1"})

        l1_before, l2_result, l1_after = result
        l2_before, l3_result, l2_after = l2_result

        assert l1_before == "level1"
        assert l2_before == "level2"
        assert l3_result == "level3"
        assert l2_after == "level2"
        assert l1_after == "level1"


# ============================================================================
# Law 2: Local Non-State-Scoping Law Tests
# ============================================================================


class TestLocalNonStateScopingLaw:
    """Tests for Law 2: Local does NOT scope state (Get/Put)."""

    @pytest.mark.asyncio
    async def test_local_does_not_scope_state(self) -> None:
        """Law 2: State changes propagate through Local scope."""
        runtime = AsyncRuntime()

        @do
        def inner_program():
            yield Put("counter", 42)
            yield Modify("list_key", lambda x: (x or []) + ["inner"])
            return "done"

        @do
        def program():
            yield Put("counter", 0)
            yield Put("list_key", ["outer"])
            yield Local({"env_key": "scoped"}, inner_program())
            counter = yield Get("counter")
            list_val = yield Get("list_key")
            return (counter, list_val)

        result = await runtime.run_and_unwrap(program())
        counter, list_val = result

        assert counter == 42
        assert list_val == ["outer", "inner"]


# ============================================================================
# Law 3: Listen Capture Law Tests
# ============================================================================


class TestListenCaptureLaw:
    """Tests for Law 3: Log/Tell operations captured on success only."""

    @pytest.mark.asyncio
    async def test_listen_captures_logs_from_local(self) -> None:
        """Law 3: Logs captured from nested Local scope on success."""
        runtime = AsyncRuntime()

        @do
        def inner_program():
            yield Tell("inner_log_1")
            yield Tell("inner_log_2")
            return "inner_result"

        @do
        def program():
            listen_result = yield Listen(Local({"key": "val"}, inner_program()))
            return listen_result

        result = await runtime.run_and_unwrap(program())

        assert result.value == "inner_result"
        assert len(result.log) == 2
        assert "inner_log_1" in result.log
        assert "inner_log_2" in result.log

    @pytest.mark.asyncio
    async def test_listen_captures_all_gather_logs(self) -> None:
        """Law 3: Logs from all Gather children captured on success."""
        runtime = AsyncRuntime()

        @do
        def task1():
            yield Tell("task1_log")
            return 1

        @do
        def task2():
            yield Tell("task2_log")
            return 2

        @do
        def task3():
            yield Tell("task3_log")
            return 3

        @do
        def program():
            listen_result = yield Listen(Gather(task1(), task2(), task3()))
            return listen_result

        result = await runtime.run_and_unwrap(program())

        assert result.value == [1, 2, 3]
        assert len(result.log) == 3
        assert "task1_log" in result.log
        assert "task2_log" in result.log
        assert "task3_log" in result.log

    @pytest.mark.asyncio
    async def test_nested_listen_separation(self) -> None:
        """Law 3: Each Listen captures logs from its sub-tree."""
        runtime = AsyncRuntime()

        @do
        def inner_program():
            yield Tell("inner_only")
            return "inner_result"

        @do
        def program():
            yield Tell("outer_before")
            inner_listen = yield Listen(inner_program())
            yield Tell("outer_after")
            outer_listen = yield Listen(Program.pure("outer_result"))
            return (inner_listen, outer_listen)

        @do
        def wrapper():
            result = yield Listen(program())
            return result

        result = await runtime.run_and_unwrap(wrapper())

        inner_listen, outer_listen = result.value

        assert "inner_only" in inner_listen.log
        assert len(inner_listen.log) == 1

        assert len(outer_listen.log) == 0

    @pytest.mark.asyncio
    async def test_listen_does_not_capture_on_error(self) -> None:
        """Law 3: On error, logs are NOT captured - error propagates directly."""
        runtime = AsyncRuntime()

        @do
        def failing_with_logs():
            yield Tell("log_before_fail")
            yield Tell("another_log")
            raise ValueError("intentional failure")

        @do
        def program():
            result = yield Safe(Listen(failing_with_logs()))
            return result

        result = await runtime.run_and_unwrap(program())

        assert result.is_err()
        assert isinstance(result.error, ValueError)


# ============================================================================
# Law 4: Safe Non-Rollback Law Tests
# ============================================================================


class TestSafeNonRollbackLaw:
    """Tests for Law 4: Safe does NOT rollback state on error."""

    @pytest.mark.asyncio
    async def test_safe_does_not_rollback_state(self) -> None:
        """Law 4: State changes persist despite Safe catching error."""
        runtime = AsyncRuntime()

        @do
        def modify_then_fail():
            yield Put("counter", 10)
            yield Tell("before_fail")
            raise ValueError("intentional error")

        @do
        def program():
            yield Put("counter", 0)
            result = yield Safe(modify_then_fail())
            counter = yield Get("counter")
            return (result.is_err(), counter)

        result = await runtime.run_and_unwrap(program())
        had_error, counter = result

        assert had_error is True
        assert counter == 10

    @pytest.mark.asyncio
    async def test_nested_safe_innermost_catches(self) -> None:
        """Law 4: Innermost Safe catches, outer Safe sees success."""
        runtime = AsyncRuntime()

        @do
        def failing_program():
            raise ValueError("inner error")

        @do
        def middle_program():
            inner_result = yield Safe(failing_program())
            return ("middle_ok", inner_result.is_err())

        @do
        def program():
            outer_result = yield Safe(middle_program())
            return outer_result

        result = await runtime.run_and_unwrap(program())

        assert result.is_ok()
        middle_status, inner_had_error = result.value
        assert middle_status == "middle_ok"
        assert inner_had_error is True


# ============================================================================
# Law 5: Safe Environment Restoration Law Tests
# ============================================================================


class TestSafeEnvironmentRestorationLaw:
    """Tests for Law 5: Safe restores environment context."""

    @pytest.mark.asyncio
    async def test_safe_with_local_restores_env(self) -> None:
        """Law 5: Environment restored even when Safe catches Local failure."""
        runtime = AsyncRuntime()

        @do
        def failing_in_local():
            val = yield Ask("key")
            raise ValueError(f"saw: {val}")

        @do
        def program():
            outer_before = yield Ask("key")
            result = yield Safe(Local({"key": "inner"}, failing_in_local()))
            outer_after = yield Ask("key")
            return (outer_before, result.is_err(), outer_after)

        result = await runtime.run_and_unwrap(program(), env={"key": "outer"})
        outer_before, had_error, outer_after = result

        assert outer_before == "outer"
        assert had_error is True
        assert outer_after == "outer"


# ============================================================================
# Law 6: Intercept Transformation Law Tests
# ============================================================================


class TestInterceptTransformationLaw:
    """Tests for Law 6: Intercept transforms effects including Gather children."""

    @pytest.mark.asyncio
    async def test_intercept_transforms_gather_children(self) -> None:
        """Law 6: Intercept DOES transform Gather children via structural rewriting."""
        runtime = AsyncRuntime()

        transform_count = [0]

        @do
        def child1():
            val = yield Ask("key")
            return f"child1:{val}"

        @do
        def child2():
            val = yield Ask("key")
            return f"child2:{val}"

        def counting_transform(effect):
            if isinstance(effect, AskEffect):
                transform_count[0] += 1
            return effect

        @do
        def program():
            results = yield Gather(child1(), child2()).intercept(counting_transform)
            return results

        result = await runtime.run_and_unwrap(program(), env={"key": "value"})

        assert result == ["child1:value", "child2:value"]
        assert transform_count[0] == 2


# ============================================================================
# Law 7: Gather Environment Inheritance Law Tests
# ============================================================================


class TestGatherEnvironmentInheritanceLaw:
    """Tests for Law 7: Gather children inherit parent's environment."""

    @pytest.mark.asyncio
    async def test_gather_children_inherit_local_env(self) -> None:
        """Law 7: Children inherit parent's environment at Gather time."""
        runtime = AsyncRuntime()

        @do
        def child1():
            val = yield Ask("key")
            return f"child1:{val}"

        @do
        def child2():
            val = yield Ask("key")
            return f"child2:{val}"

        @do
        def program():
            outer_result = yield Ask("key")
            inner_results = yield Local(
                {"key": "local_value"},
                Gather(child1(), child2())
            )
            return (outer_result, inner_results)

        result = await runtime.run_and_unwrap(program(), env={"key": "outer_value"})
        outer_result, inner_results = result

        assert outer_result == "outer_value"
        assert inner_results == ["child1:local_value", "child2:local_value"]


# ============================================================================
# Law 8: Gather Store Sharing Law Tests
# ============================================================================


class TestGatherStoreSharingLaw:
    """Tests for Law 8: Gather store sharing (runtime-dependent)."""

    def test_sync_gather_sequential_store_sharing(self) -> None:
        """Law 8a: SyncRuntime Gather is sequential with deterministic state."""
        runtime = SyncRuntime()

        @do
        def task1():
            current = yield Get("counter")
            yield Put("counter", (current or 0) + 1)
            return current

        @do
        def task2():
            current = yield Get("counter")
            yield Put("counter", (current or 0) + 1)
            return current

        @do
        def task3():
            current = yield Get("counter")
            yield Put("counter", (current or 0) + 1)
            return current

        @do
        def program():
            yield Put("counter", 0)
            results = yield Gather(task1(), task2(), task3())
            final = yield Get("counter")
            return (results, final)

        results, final = runtime.run(program()).value

        assert results == [0, 1, 2]
        assert final == 3

    @pytest.mark.asyncio
    async def test_async_gather_parallel_execution(self) -> None:
        """Law 8b: AsyncRuntime Gather executes in parallel."""
        runtime = AsyncRuntime()

        @do
        def delayed_task(n: int):
            yield Delay(seconds=0.05)
            return n

        @do
        def program():
            start = yield GetTime()
            results = yield Gather(
                delayed_task(1),
                delayed_task(2),
                delayed_task(3),
            )
            end = yield GetTime()
            elapsed = (end - start).total_seconds()
            return (results, elapsed)

        results, elapsed = await runtime.run_and_unwrap(program())

        assert results == [1, 2, 3]
        assert elapsed < 0.2


# ============================================================================
# Law 9: Gather Error Propagation Law Tests
# ============================================================================


class TestGatherErrorPropagationLaw:
    """Tests for Law 9: Gather error propagation behavior."""

    @pytest.mark.asyncio
    async def test_gather_error_propagation(self) -> None:
        """Law 9: Gather propagates first child error to parent."""
        runtime = AsyncRuntime()

        side_effects = []

        @do
        def succeeds_first():
            side_effects.append("first_started")
            yield Put("first_done", True)
            side_effects.append("first_done")
            return "first"

        @do
        def fails():
            side_effects.append("fail_started")
            raise ValueError("child failed")

        @do
        def succeeds_last():
            side_effects.append("last_started")
            yield Put("last_done", True)
            side_effects.append("last_done")
            return "last"

        @do
        def program():
            result = yield Safe(Gather(
                succeeds_first(),
                fails(),
                succeeds_last(),
            ))
            return result

        result = await runtime.run_and_unwrap(program())

        assert result.is_err()
        assert isinstance(result.error, ValueError)


# ============================================================================
# Integration Tests
# ============================================================================


class TestEffectCombinationIntegration:
    """Integration tests for complex effect combinations."""

    @pytest.mark.asyncio
    async def test_nested_gather_parallelism(self) -> None:
        """Integration: Nested Gather operations work correctly."""
        runtime = AsyncRuntime()

        @do
        def leaf_task(n: int):
            yield Tell(f"leaf_{n}")
            return n

        @do
        def branch_a():
            results = yield Gather(leaf_task(1), leaf_task(2))
            return ("a", results)

        @do
        def branch_b():
            results = yield Gather(leaf_task(3), leaf_task(4))
            return ("b", results)

        @do
        def program():
            listen_result = yield Listen(Gather(branch_a(), branch_b()))
            return listen_result

        result = await runtime.run_and_unwrap(program())

        assert len(result.value) == 2
        branch_a_result, branch_b_result = result.value

        assert branch_a_result == ("a", [1, 2])
        assert branch_b_result == ("b", [3, 4])

        assert len(result.log) == 4
        for i in range(1, 5):
            assert f"leaf_{i}" in result.log

    @pytest.mark.asyncio
    async def test_complex_safe_local_listen_combination(self) -> None:
        """Integration: Complex combination of Safe, Local, and Listen."""
        runtime = AsyncRuntime()

        @do
        def inner_task():
            config = yield Ask("config")
            yield Tell(f"config={config}")
            yield Put("processed", True)
            if config == "fail":
                raise ValueError("config was fail")
            return f"result:{config}"

        @do
        def program():
            yield Put("processed", False)

            result = yield Safe(
                Local(
                    {"config": "success"},
                    Listen(inner_task())
                )
            )

            outer_config = yield Ask("config")
            processed = yield Get("processed")

            return (result, outer_config, processed)

        result = await runtime.run_and_unwrap(program(), env={"config": "outer"})
        safe_result, outer_config, processed = result

        assert safe_result.is_ok()
        listen_result = safe_result.value
        assert listen_result.value == "result:success"
        assert "config=success" in listen_result.log
        assert outer_config == "outer"
        assert processed is True
