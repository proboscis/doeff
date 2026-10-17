"""Tests for effect combination behaviors.

This test module validates the composition laws defined in SPEC-EFF-100-combinations.md.
It tests how effects interact when nested or composed, ensuring consistent and
predictable behavior.

Reference: gh#180, docs/history/specs/effects/SPEC-EFF-100-combinations.md
"""

import asyncio
import time

import pytest
from doeff_core_effects import SlogEffect, WriterTellEffect, slog, writer_log
from doeff_core_effects.handlers import (
    listen_handler,
    slog_discard_handler,
    state,
    try_handler,
    writer,
)

from doeff import (
    Ask,
    Await,
    Gather,
    Get,
    Listen,
    Local,
    Put,
    Spawn,
    Tell,
    Try,
    do,
    run,
)
from tests._run_helpers import run_with_defaults

# ============================================================================
# Law 1: Local Restoration Law Tests
# ============================================================================


class TestLocalRestorationLaw:
    """Tests for Law 1: Environment MUST restore after Local scope."""

    def test_local_restores_env_on_success(self) -> None:
        """Law 1: Environment MUST restore after Local scope, regardless of success."""

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

        result = run_with_defaults(program(), env={"key": "outer_value"})
        assert result.is_ok()
        outer_before, inner_result, outer_after = result.value

        assert outer_before == "outer_value"
        assert inner_result == "inner_value"
        assert outer_after == "outer_value"

    def test_local_restores_env_on_error(self) -> None:
        """Law 1: Environment MUST restore after Local scope, regardless of error."""

        @do
        def failing_inner():
            yield Ask("key")
            raise ValueError("inner error")

        @do
        def program():
            outer_before = yield Ask("key")
            result = yield Try(Local({"key": "inner_value"}, failing_inner()))
            outer_after = yield Ask("key")
            return (outer_before, result.is_err(), outer_after)

        result = run_with_defaults(program(), env={"key": "outer_value"})
        assert result.is_ok()
        outer_before, had_error, outer_after = result.value

        assert outer_before == "outer_value"
        assert had_error is True
        assert outer_after == "outer_value"

    def test_nested_local_override_and_restore(self) -> None:
        """Law 1: Each Local scope independently restores its environment."""

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

        result = run_with_defaults(level1(), env={"key": "level1"})
        assert result.is_ok()

        l1_before, l2_result, l1_after = result.value
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

    def test_local_does_not_scope_state(self) -> None:
        """Law 2: State changes propagate through Local scope."""

        @do
        def inner_program():
            yield Put("counter", 42)
            old_list = yield Get("list_key")
            yield Put("list_key", (old_list or []) + ["inner"])
            return "done"

        @do
        def program():
            yield Put("counter", 0)
            yield Put("list_key", ["outer"])
            yield Local({"env_key": "scoped"}, inner_program())
            counter = yield Get("counter")
            list_val = yield Get("list_key")
            return (counter, list_val)

        result = run_with_defaults(program())
        assert result.is_ok()
        counter, list_val = result.value

        assert counter == 42
        assert list_val == ["outer", "inner"]


# ============================================================================
# Law 3: Listen Capture Law Tests
# ============================================================================


class TestListenCaptureLaw:
    """Tests for Law 3: Log/Tell operations captured on success only."""

    def test_listen_captures_logs_from_local(self) -> None:
        """Law 3: Logs captured from nested Local scope on success."""

        @do
        def inner_program():
            yield Tell("inner_log_1")
            yield Tell("inner_log_2")
            return "inner_result"

        @do
        def program():
            listen_result = yield Listen(Local({"key": "val"}, inner_program()))
            return listen_result

        result = run_with_defaults(program())
        assert result.is_ok()

        inner_value, collected = result.value
        assert inner_value == "inner_result"
        assert len(collected) == 2
        msgs = [getattr(e, "msg", None) for e in collected]
        assert "inner_log_1" in msgs
        assert "inner_log_2" in msgs

    @pytest.mark.skip(reason="scheduler does not propagate Listen to spawned tasks in rebuild")
    def test_gather_shared_state_logs_are_captured(self) -> None:
        """Gather shares branch handler state, so parent Listen observes branch logs."""

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
            f1 = yield Spawn(task1())
            f2 = yield Spawn(task2())
            f3 = yield Spawn(task3())
            listen_result = yield Listen(Gather(f1, f2, f3))
            return listen_result

        result = run_with_defaults(program())
        assert result.is_ok()

        inner_value, collected = result.value
        assert inner_value == [1, 2, 3]
        msgs = sorted(getattr(e, "msg", None) for e in collected)
        assert msgs == ["task1_log", "task2_log", "task3_log"]

    def test_listen_does_not_capture_on_error(self) -> None:
        """Law 3: On error, logs are NOT captured - error propagates directly."""

        @do
        def failing_with_logs():
            yield Tell("log_before_fail")
            yield Tell("another_log")
            raise ValueError("intentional failure")

        @do
        def program():
            result = yield Try(Listen(failing_with_logs()))
            return result

        result = run_with_defaults(program())
        assert result.is_ok()

        safe_result = result.value
        assert safe_result.is_err()
        assert isinstance(safe_result.error, ValueError)

    def test_listen_of_try_keeps_entries_before_the_caught_error(self) -> None:
        """SPEC-EFF-003 "Listen + Try": Listen(Try(prog)) returns Err and the entries before it."""

        @do
        def failing_with_logs():
            yield Tell("before_error")
            raise ValueError("intentional failure")

        @do
        def program():
            return (yield Listen(Try(failing_with_logs())))

        result = run_with_defaults(program())
        assert result.is_ok()

        caught, collected = result.value
        assert caught.is_err()
        assert isinstance(caught.error, ValueError)
        assert [entry.msg for entry in collected] == ["before_error"]

    def test_listen_captures_tasks_spawned_inside_its_scope(self) -> None:
        """SPEC-EFF-003 "Listen + Spawn/Gather": a task spawned in the scope tells inside it."""

        @do
        def task(name):
            yield Tell(f"{name}_start")
            yield Tell(f"{name}_end")
            return name

        @do
        def scope():
            first = yield Spawn(task("A"))
            second = yield Spawn(task("B"))
            return (yield Gather(first, second))

        @do
        def program():
            return (yield Listen(scope()))

        result = run_with_defaults(program())
        assert result.is_ok()

        values, collected = result.value
        assert values == ["A", "B"]
        assert [entry.msg for entry in collected] == ["A_start", "A_end", "B_start", "B_end"]

    def test_listen_does_not_capture_tasks_spawned_before_its_scope(self) -> None:
        """SPEC-EFF-003 "Listen + Spawn/Gather": waiting inside the scope does not move the Tells."""

        @do
        def task(name):
            yield Tell(f"{name}_told")
            return name

        @do
        def program():
            first = yield Spawn(task("A"))
            second = yield Spawn(task("B"))
            return (yield Listen(Gather(first, second)))

        result = run_with_defaults(program())
        assert result.is_ok()

        values, collected = result.value
        assert values == ["A", "B"]
        assert collected == []


def _tells(collected) -> list[object]:
    """The messages of the collected Tell effects, in order."""
    return [entry.msg for entry in collected]


class TestListenNested:
    """SPEC-EFF-003 "Listen + Listen (nested)": one listen_handler serves every nesting level.

    The inner Listen captures only its own scope; the outer Listen captures its whole scope,
    including what the inner scope told. A single ``listen_handler`` installed once is enough --
    it installs itself again around each Listen's sub-program (as ``try_handler`` does for Try).
    """

    def test_inner_captures_own_scope_and_outer_captures_all(self) -> None:
        @do
        def inner():
            yield Tell("inner1")
            yield Tell("inner2")
            return "inner_result"

        @do
        def middle():
            yield Tell("middle1")
            inner_listen = yield Listen(inner())
            yield Tell("middle2")
            return inner_listen

        @do
        def outer():
            return (yield Listen(middle()))

        # The one listen_handler a program installs, exactly as chapter 03 writes it.
        value = run(state()(writer(listen_handler(outer()))))

        (inner_value, inner_collected), outer_collected = value
        assert inner_value == "inner_result"
        assert _tells(inner_collected) == ["inner1", "inner2"]
        assert _tells(outer_collected) == ["middle1", "inner1", "inner2", "middle2"]

    def test_nested_listen_works_in_the_default_handler_chain(self) -> None:
        @do
        def inner():
            yield Tell("inner")
            return "inner_result"

        @do
        def outer():
            yield Tell("before")
            inner_result = yield Listen(inner())
            yield Tell("after")
            return inner_result

        @do
        def program():
            return (yield Listen(outer()))

        result = run_with_defaults(program())
        assert result.is_ok()
        (inner_value, inner_collected), outer_collected = result.value
        assert inner_value == "inner_result"
        assert _tells(inner_collected) == ["inner"]
        assert _tells(outer_collected) == ["before", "inner", "after"]

    def test_three_levels_deep(self) -> None:
        @do
        def level3():
            yield Tell("l3")
            return "l3_result"

        @do
        def level2():
            yield Tell("l2-before")
            nested = yield Listen(level3())
            yield Tell("l2-after")
            return nested

        @do
        def level1():
            yield Tell("l1-before")
            nested = yield Listen(level2())
            yield Tell("l1-after")
            return nested

        @do
        def program():
            return (yield Listen(level1()))

        value = run(state()(writer(listen_handler(program()))))

        ((l3_value, l3_collected), l2_collected), l1_collected = value
        assert l3_value == "l3_result"
        assert _tells(l3_collected) == ["l3"]
        assert _tells(l2_collected) == ["l2-before", "l3", "l2-after"]
        assert _tells(l1_collected) == ["l1-before", "l2-before", "l3", "l2-after", "l1-after"]

    def test_sibling_listens_inside_one_listen_capture_separately(self) -> None:
        @do
        def part(name):
            yield Tell(f"{name}-1")
            yield Tell(f"{name}-2")
            return name

        @do
        def parts():
            first = yield Listen(part("a"))
            second = yield Listen(part("b"))
            return first, second

        @do
        def program():
            return (yield Listen(parts()))

        value = run(state()(writer(listen_handler(program()))))

        ((first, second), outer_collected) = value
        assert (first[0], _tells(first[1])) == ("a", ["a-1", "a-2"])
        assert (second[0], _tells(second[1])) == ("b", ["b-1", "b-2"])
        assert _tells(outer_collected) == ["a-1", "a-2", "b-1", "b-2"]

    def test_each_level_collects_its_own_types(self) -> None:
        @do
        def inner():
            yield Tell("told")
            yield slog("logged", step=1)
            return "inner_result"

        @do
        def outer():
            return (yield Listen(inner(), types=(SlogEffect,)))

        @do
        def program():
            return (yield Listen(outer(), types=(WriterTellEffect, SlogEffect)))

        value = run(state()(writer(slog_discard_handler(listen_handler(program())))))

        (inner_value, inner_collected), outer_collected = value
        assert inner_value == "inner_result"
        assert [(e.msg, e.kwargs) for e in inner_collected] == [("logged", {"step": 1})]
        assert [type(e) for e in outer_collected] == [WriterTellEffect, SlogEffect]

    def test_entries_of_nested_listens_stay_in_the_writer_log(self) -> None:
        @do
        def inner():
            yield Tell("inner")

        @do
        def outer():
            yield Tell("outer-before")
            yield Listen(inner())
            yield Tell("outer-after")

        @do
        def program():
            yield Listen(outer())
            return (yield writer_log())

        assert run(state()(writer(listen_handler(program())))) == [
            "outer-before",
            "inner",
            "outer-after",
        ]

    def test_nested_listen_under_try_keeps_entries_before_the_error(self) -> None:
        @do
        def failing_inner():
            yield Tell("before_error")
            raise ValueError("intentional failure")

        @do
        def outer():
            yield Tell("outer")
            caught = yield Try(Listen(failing_inner()))
            return caught

        @do
        def program():
            return (yield Listen(outer()))

        value = run(state()(writer(try_handler(listen_handler(program())))))

        (caught, outer_collected) = value
        assert caught.is_err()
        assert isinstance(caught.error, ValueError)
        assert _tells(outer_collected) == ["outer", "before_error"]


# ============================================================================
# Law 4: Try Non-Rollback Law Tests
# ============================================================================


class TestSafeNonRollbackLaw:
    """Tests for Law 4: Try does NOT rollback state on error."""

    def test_safe_does_not_rollback_state(self) -> None:
        """Law 4: State changes persist despite Try catching error."""

        @do
        def modify_then_fail():
            yield Put("counter", 10)
            yield Tell("before_fail")
            raise ValueError("intentional error")

        @do
        def program():
            yield Put("counter", 0)
            result = yield Try(modify_then_fail())
            counter = yield Get("counter")
            return (result.is_err(), counter)

        result = run_with_defaults(program())
        assert result.is_ok()
        had_error, counter = result.value

        assert had_error is True
        assert counter == 10

    def test_nested_safe_innermost_catches(self) -> None:
        """Law 4: Innermost Try catches, outer Try sees success."""

        @do
        def failing_program():
            raise ValueError("inner error")

        @do
        def middle_program():
            inner_result = yield Try(failing_program())
            return ("middle_ok", inner_result.is_err())

        @do
        def program():
            outer_result = yield Try(middle_program())
            return outer_result

        result = run_with_defaults(program())
        assert result.is_ok()

        outer_result = result.value
        assert outer_result.is_ok()
        middle_status, inner_had_error = outer_result.value
        assert middle_status == "middle_ok"
        assert inner_had_error is True


# ============================================================================
# Law 5: Try Environment Restoration Law Tests
# ============================================================================


class TestSafeEnvironmentRestorationLaw:
    """Tests for Law 5: Try restores environment context."""

    def test_safe_with_local_restores_env(self) -> None:
        """Law 5: Environment restored even when Try catches Local failure."""

        @do
        def failing_in_local():
            val = yield Ask("key")
            raise ValueError(f"saw: {val}")

        @do
        def program():
            outer_before = yield Ask("key")
            result = yield Try(Local({"key": "inner"}, failing_in_local()))
            outer_after = yield Ask("key")
            return (outer_before, result.is_err(), outer_after)

        result = run_with_defaults(program(), env={"key": "outer"})
        assert result.is_ok()
        outer_before, had_error, outer_after = result.value

        assert outer_before == "outer"
        assert had_error is True
        assert outer_after == "outer"


# ============================================================================
# Law 7: Gather Environment Inheritance Law Tests
# ============================================================================


class TestGatherEnvironmentInheritanceLaw:
    """Tests for Law 7: Gather children inherit parent's environment."""

    def test_gather_children_inherit_local_env(self) -> None:
        """Law 7: Children inherit parent's environment at Gather time."""

        @do
        def child1():
            val = yield Ask("key")
            return f"child1:{val}"

        @do
        def child2():
            val = yield Ask("key")
            return f"child2:{val}"

        @do
        def gather_children():
            t1 = yield Spawn(child1())
            t2 = yield Spawn(child2())
            return (yield Gather(t1, t2))

        @do
        def program():
            outer_result = yield Ask("key")
            inner_results = yield Local({"key": "local_value"}, gather_children())
            return (outer_result, inner_results)

        result = run_with_defaults(program(), env={"key": "outer_value"})
        assert result.is_ok()
        outer_result, inner_results = result.value

        assert outer_result == "outer_value"
        assert inner_results == ["child1:local_value", "child2:local_value"]


# ============================================================================
# Law 8: Gather Store Sharing Law Tests
# ============================================================================


class TestGatherStoreSharingLaw:
    """Tests for Law 8: Gather store sharing (runtime-dependent)."""

    @pytest.mark.realtime
    def test_async_gather_parallel_execution(self) -> None:
        """Law 8b: Gather executes spawned tasks in parallel."""

        @do
        def delayed_task(n: int):
            yield Await(asyncio.sleep(0.05))
            return n

        @do
        def program():
            t1 = yield Spawn(delayed_task(1))
            t2 = yield Spawn(delayed_task(2))
            t3 = yield Spawn(delayed_task(3))
            results = yield Gather(t1, t2, t3)
            return results

        start = time.time()
        result = run_with_defaults(program())
        elapsed = time.time() - start

        assert result.is_ok()
        results = result.value

        assert results == [1, 2, 3]
        # If executed in parallel, all should complete in ~0.05s, not 0.15s
        assert elapsed < 0.2


# ============================================================================
# Law 9: Gather Error Propagation Law Tests
# ============================================================================


class TestGatherErrorPropagationLaw:
    """Tests for Law 9: Gather error propagation behavior."""

    def test_gather_error_propagation(self) -> None:
        """Law 9: Gather propagates first child error to parent."""
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
        def gather_children():
            t1 = yield Spawn(succeeds_first())
            t2 = yield Spawn(fails())
            t3 = yield Spawn(succeeds_last())
            return (yield Gather(t1, t2, t3))

        @do
        def program():
            result = yield Try(gather_children())
            return result

        result = run_with_defaults(program())
        assert result.is_ok()

        safe_result = result.value
        assert safe_result.is_err()
        assert isinstance(safe_result.error, ValueError)


# ============================================================================
# Integration Tests
# ============================================================================


class TestEffectCombinationIntegration:
    """Integration tests for complex effect combinations."""

    def test_nested_gather_parallelism(self) -> None:
        """Integration: Nested Gather operations work correctly."""

        @do
        def leaf_task(n: int):
            return n

        @do
        def branch_a():
            t1 = yield Spawn(leaf_task(1))
            t2 = yield Spawn(leaf_task(2))
            results = yield Gather(t1, t2)
            return ("a", results)

        @do
        def branch_b():
            t1 = yield Spawn(leaf_task(3))
            t2 = yield Spawn(leaf_task(4))
            results = yield Gather(t1, t2)
            return ("b", results)

        @do
        def program():
            t1 = yield Spawn(branch_a())
            t2 = yield Spawn(branch_b())
            results = yield Gather(t1, t2)
            return results

        result = run_with_defaults(program())
        assert result.is_ok()

        assert len(result.value) == 2
        branch_a_result, branch_b_result = result.value

        assert branch_a_result == ("a", [1, 2])
        assert branch_b_result == ("b", [3, 4])

    def test_complex_safe_local_listen_combination(self) -> None:
        """Integration: Complex combination of Try, Local, and Listen."""

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

            result = yield Try(Local({"config": "success"}, Listen(inner_task())))

            outer_config = yield Ask("config")
            processed = yield Get("processed")

            return (result, outer_config, processed)

        result = run_with_defaults(program(), env={"config": "outer"})
        assert result.is_ok()
        safe_result, outer_config, processed = result.value

        assert safe_result.is_ok()
        inner_value, collected = safe_result.value
        assert inner_value == "result:success"
        msgs = [getattr(e, "msg", None) for e in collected]
        assert "config=success" in msgs
        assert outer_config == "outer"
        assert processed is True
