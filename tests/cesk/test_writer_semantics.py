"""Tests for Writer effect semantics (SPEC-EFF-003-writer).

This module tests the composition rules and semantics for Writer effects:
- Tell - append messages to the writer log
- Listen - capture logs from a sub-program

Tested compositions:
- Listen + Tell
- Listen + Local
- Listen + Safe (success and error)
- Listen + Gather (sync and async)
- Listen + Listen (nested)

Reference: specs/effects/SPEC-EFF-003-writer.md
Related issue: gh#176
"""

import pytest

from doeff import Program, do
from doeff.effects import (
    Ask,
    Gather,
    Get,
    Listen,
    Local,
    Put,
    Safe,
    Spawn,
    Tell,
)
from doeff.effects.writer import StructuredLog, slog


class TestWriterBasics:
    """Basic Writer effect tests."""

    def test_log_appends_to_store(self) -> None:
        """Log effect appends message to __log__ in store."""
        from doeff.cesk.run import sync_handlers_preset, sync_run

        

        @do
        def program():
            yield Tell("message1")
            yield Tell("message2")
            return "done"

        result = sync_run(program(), sync_handlers_preset)
        assert result.value == "done"

    def test_tell_appends_to_store(self) -> None:
        """Tell effect appends message to __log__ in store."""
        from doeff.cesk.run import sync_handlers_preset, sync_run

        

        @do
        def program():
            yield Tell("message1")
            yield Tell({"key": "value"})
            return "done"

        result = sync_run(program(), sync_handlers_preset)
        assert result.value == "done"

    def test_log_and_tell_are_equivalent(self) -> None:
        """Log and Tell produce identical effects."""
        from doeff.cesk.run import sync_handlers_preset, sync_run

        

        @do
        def program_with_log():
            yield Tell("test")
            result = yield Listen(Program.pure("inner"))
            return result

        @do
        def program_with_tell():
            yield Tell("test")
            result = yield Listen(Program.pure("inner"))
            return result

        # Verify both behave identically
        result1 = sync_run(program_with_log(), sync_handlers_preset)
        result2 = sync_run(program_with_tell(), sync_handlers_preset)

        assert result1.value.value == result2.value.value

    def test_structured_log(self) -> None:
        """StructuredLog creates dictionary log entry."""
        from doeff.cesk.run import sync_handlers_preset, sync_run

        

        @do
        def inner():
            yield StructuredLog(action="test", value=42)
            return "done"

        @do
        def program():
            result = yield Listen(inner())
            return result

        result = sync_run(program(), sync_handlers_preset)
        listen_result = result.value
        assert listen_result.value == "done"
        assert len(listen_result.log) == 1
        assert listen_result.log[0] == {"action": "test", "value": 42}

    def test_slog_alias(self) -> None:
        """slog is an alias for StructuredLog."""
        from doeff.cesk.run import sync_handlers_preset, sync_run

        

        @do
        def inner():
            yield slog(action="test", count=5)
            return "done"

        @do
        def program():
            result = yield Listen(inner())
            return result

        result = sync_run(program(), sync_handlers_preset)
        listen_result = result.value
        assert listen_result.log[0] == {"action": "test", "count": 5}


class TestListenPlusLog:
    """Test Listen + Log composition."""

    def test_listen_captures_single_log(self) -> None:
        """Listen captures a single log message."""
        from doeff.cesk.run import sync_handlers_preset, sync_run

        

        @do
        def inner():
            yield Tell("captured")
            return "result"

        @do
        def program():
            result = yield Listen(inner())
            return result

        result = sync_run(program(), sync_handlers_preset)
        listen_result = result.value
        assert listen_result.value == "result"
        assert list(listen_result.log) == ["captured"]

    def test_listen_captures_multiple_logs(self) -> None:
        """Listen captures multiple log messages in order."""
        from doeff.cesk.run import sync_handlers_preset, sync_run

        

        @do
        def inner():
            yield Tell("first")
            yield Tell("second")
            yield Tell("third")
            return 42

        @do
        def program():
            result = yield Listen(inner())
            return result

        result = sync_run(program(), sync_handlers_preset)
        listen_result = result.value
        assert listen_result.value == 42
        assert list(listen_result.log) == ["first", "second", "third"]

    def test_listen_captures_only_sub_program_logs(self) -> None:
        """Listen only captures logs from its sub-program, not outer logs."""
        from doeff.cesk.run import sync_handlers_preset, sync_run

        

        @do
        def inner():
            yield Tell("inner")
            return "inner_result"

        @do
        def program():
            yield Tell("before")
            result = yield Listen(inner())
            yield Tell("after")
            return result

        result = sync_run(program(), sync_handlers_preset)
        listen_result = result.value
        assert listen_result.value == "inner_result"
        # Listen only captures "inner", not "before" or "after"
        assert list(listen_result.log) == ["inner"]


class TestListenPlusLocal:
    """Test Listen + Local composition."""

    @pytest.mark.xfail(
        reason="SPEC-CESK-003: Handler-based patterns create nested contexts when forwarding. "
               "Effects may bypass intermediate handlers, breaking expected composition semantics."
    )
    def test_listen_captures_logs_within_local(self) -> None:
        """Logs within Local scope are captured by Listen."""
        from doeff.cesk.run import sync_handlers_preset, sync_run

        

        @do
        def inner():
            key = yield Ask("key")
            yield Tell(f"key is {key}")
            return key

        @do
        def program():
            result = yield Listen(Local({"key": "local_value"}, inner()))
            return result

        result = sync_run(program(), sync_handlers_preset)
        listen_result = result.value
        assert listen_result.value == "local_value"
        assert list(listen_result.log) == ["key is local_value"]

    @pytest.mark.xfail(
        reason="SPEC-CESK-003: Handler-based patterns create nested contexts when forwarding. "
               "Effects may bypass intermediate handlers, breaking expected composition semantics."
    )
    def test_listen_around_local_captures_all(self) -> None:
        """Listen wrapping Local captures all logs from within."""
        from doeff.cesk.run import sync_handlers_preset, sync_run

        

        @do
        def inner():
            yield Tell("inside_local")
            return "done"

        @do
        def program():
            result = yield Listen(Local({"x": 1}, inner()))
            return result

        result = sync_run(program(), sync_handlers_preset)
        listen_result = result.value
        assert list(listen_result.log) == ["inside_local"]


class TestListenPlusSafe:
    """Test Listen + Safe composition."""

    @pytest.mark.xfail(reason="SPEC-CESK-003: Handler-based patterns create nested contexts when forwarding. Effects may bypass intermediate handlers, breaking expected composition semantics.")
    def test_listen_plus_safe_success(self) -> None:
        """Listen + Safe captures logs on successful execution."""
        from doeff.cesk.run import sync_handlers_preset, sync_run

        

        @do
        def inner():
            yield Tell("processing")
            return 42

        @do
        def program():
            result = yield Listen(Safe(inner()))
            return result

        result = sync_run(program(), sync_handlers_preset)
        listen_result = result.value
        assert listen_result.value.is_ok()
        assert listen_result.value.unwrap() == 42
        assert list(listen_result.log) == ["processing"]

    @pytest.mark.xfail(reason="SPEC-CESK-003: Handler-based patterns create nested contexts when forwarding. Effects may bypass intermediate handlers, breaking expected composition semantics.")
    def test_listen_plus_safe_error_preserves_logs(self) -> None:
        """Listen + Safe preserves logs even when Safe catches an error."""
        from doeff.cesk.run import sync_handlers_preset, sync_run

        

        @do
        def inner():
            yield Tell("before_error")
            yield Tell("also_logged")
            raise ValueError("test error")

        @do
        def program():
            result = yield Listen(Safe(inner()))
            return result

        result = sync_run(program(), sync_handlers_preset)
        listen_result = result.value
        # The value is an Err result
        assert listen_result.value.is_err()
        assert isinstance(listen_result.value.error, ValueError)
        # But logs are preserved!
        assert list(listen_result.log) == ["before_error", "also_logged"]

    def test_safe_plus_listen(self) -> None:
        """Safe wrapping Listen also preserves logs on error."""
        from doeff.cesk.run import sync_handlers_preset, sync_run

        

        @do
        def inner():
            yield Tell("logged_before_fail")
            raise RuntimeError("oops")

        @do
        def program():
            # Safe around Listen
            safe_result = yield Safe(Listen(inner()))
            return safe_result

        result = sync_run(program(), sync_handlers_preset)
        safe_result = result.value
        # Safe catches the error
        assert safe_result.is_err()


class TestListenPlusGather:
    """Test Listen + Gather composition."""

    @pytest.mark.skip(
        reason="Gather now requires Futures from Spawn, SyncRuntime doesn't support Spawn yet. "
        "NOTE: SyncRuntime could implement Spawn/Gather via cooperative scheduling in the future."
    )
    def test_listen_gather_sync_sequential_logs(self) -> None:
        pass

    @pytest.mark.asyncio
    async def test_listen_gather_async_logs_not_captured(self) -> None:
        """In AsyncRuntime with Spawn+Gather, logs are NOT captured due to isolated state."""
        from doeff.cesk.run import async_handlers_preset, async_run

        

        @do
        def task(name: str):
            yield Tell(f"{name}_log")
            return name

        @do
        def program():
            t1 = yield Spawn(task("X"))
            t2 = yield Spawn(task("Y"))
            result = yield Listen(Gather(t1, t2))
            return result

        result = (await async_run(program(), async_handlers_preset)).value
        assert sorted(result.value) == ["X", "Y"]
        assert len(result.log) == 0

    def test_listen_empty_gather(self) -> None:
        """Listen with empty Gather produces empty log."""
        from doeff.cesk.run import sync_handlers_preset, sync_run

        

        @do
        def program():
            result = yield Listen(Gather())
            return result

        result = sync_run(program(), sync_handlers_preset)
        listen_result = result.value
        assert listen_result.value == []
        assert list(listen_result.log) == []


class TestNestedListen:
    """Test nested Listen (Listen + Listen) composition."""

    @pytest.mark.xfail(reason="SPEC-CESK-003: Handler-based patterns create nested contexts when forwarding. Effects may bypass intermediate handlers, breaking expected composition semantics.")
    def test_nested_listen_inner_captures_inner_logs(self) -> None:
        """Inner Listen captures only its scope logs."""
        from doeff.cesk.run import sync_handlers_preset, sync_run

        

        @do
        def inner():
            yield Tell("inner1")
            yield Tell("inner2")
            return "inner_result"

        @do
        def middle():
            yield Tell("middle_before")
            inner_result = yield Listen(inner())
            yield Tell("middle_after")
            return inner_result

        @do
        def program():
            result = yield Listen(middle())
            return result

        result = sync_run(program(), sync_handlers_preset)
        outer_listen = result.value
        # Outer listen captures all logs in its scope
        assert list(outer_listen.log) == ["middle_before", "inner1", "inner2", "middle_after"]
        # Result value is the inner ListenResult
        inner_listen_result = outer_listen.value
        assert inner_listen_result.value == "inner_result"
        # Inner ListenResult only contains inner logs
        assert list(inner_listen_result.log) == ["inner1", "inner2"]

    @pytest.mark.xfail(reason="SPEC-CESK-003: Handler-based patterns create nested contexts when forwarding. Effects may bypass intermediate handlers, breaking expected composition semantics.")
    def test_triple_nested_listen(self) -> None:
        """Three levels of nested Listen work correctly."""
        from doeff.cesk.run import sync_handlers_preset, sync_run

        

        @do
        def level3():
            yield Tell("L3")
            return "level3"

        @do
        def level2():
            yield Tell("L2_before")
            l3 = yield Listen(level3())
            yield Tell("L2_after")
            return l3

        @do
        def level1():
            yield Tell("L1_before")
            l2 = yield Listen(level2())
            yield Tell("L1_after")
            return l2

        @do
        def program():
            result = yield Listen(level1())
            return result

        result = sync_run(program(), sync_handlers_preset)
        outer_listen = result.value

        # Outermost captures all
        assert list(outer_listen.log) == ["L1_before", "L2_before", "L3", "L2_after", "L1_after"]

        # Level 1 result
        l1_result = outer_listen.value
        assert list(l1_result.log) == ["L2_before", "L3", "L2_after"]

        # Level 2 result (nested in Level 1)
        l2_result = l1_result.value
        assert list(l2_result.log) == ["L3"]
        assert l2_result.value == "level3"

    def test_sibling_listens(self) -> None:
        """Two sibling Listen calls capture their respective logs."""
        from doeff.cesk.run import sync_handlers_preset, sync_run

        

        @do
        def task_a():
            yield Tell("A1")
            yield Tell("A2")
            return "A"

        @do
        def task_b():
            yield Tell("B1")
            return "B"

        @do
        def program():
            result_a = yield Listen(task_a())
            result_b = yield Listen(task_b())
            return (result_a, result_b)

        result = sync_run(program(), sync_handlers_preset)
        result_a, result_b = result.value

        assert result_a.value == "A"
        assert list(result_a.log) == ["A1", "A2"]

        assert result_b.value == "B"
        assert list(result_b.log) == ["B1"]


class TestWriterWithState:
    """Test Writer effects interacting with State effects."""

    def test_listen_with_state_changes(self) -> None:
        """Listen captures logs while state is being modified."""
        from doeff.cesk.run import sync_handlers_preset, sync_run

        

        @do
        def inner():
            yield Put("counter", 0)
            yield Tell("initialized")
            yield Put("counter", 1)
            yield Tell("incremented")
            value = yield Get("counter")
            return value

        @do
        def program():
            result = yield Listen(inner())
            return result

        result = sync_run(program(), sync_handlers_preset)
        listen_result = result.value
        assert listen_result.value == 1
        assert list(listen_result.log) == ["initialized", "incremented"]

    def test_state_persists_across_listen(self) -> None:
        """State changes within Listen persist after Listen completes."""
        from doeff.cesk.run import sync_handlers_preset, sync_run

        

        @do
        def inner():
            yield Put("key", "inner_value")
            yield Tell("set_key")
            return "done"

        @do
        def program():
            yield Put("key", "initial")
            _ = yield Listen(inner())
            final = yield Get("key")
            return final

        result = sync_run(program(), sync_handlers_preset)
        assert result.value == "inner_value"


class TestListenResultStructure:
    """Test the structure of ListenResult."""

    def test_listen_result_has_value_and_log(self) -> None:
        """ListenResult contains value and log attributes."""
        from doeff.cesk.run import sync_handlers_preset, sync_run

        

        @do
        def inner():
            yield Tell("test")
            return {"data": 123}

        @do
        def program():
            result = yield Listen(inner())
            return result

        result = sync_run(program(), sync_handlers_preset)
        listen_result = result.value
        assert hasattr(listen_result, "value")
        assert hasattr(listen_result, "log")
        assert listen_result.value == {"data": 123}
        assert "test" in listen_result.log

    def test_listen_result_log_is_iterable(self) -> None:
        """ListenResult.log can be iterated and converted to list."""
        from doeff.cesk.run import sync_handlers_preset, sync_run

        

        @do
        def inner():
            yield Tell(1)
            yield Tell(2)
            yield Tell(3)
            return "done"

        @do
        def program():
            result = yield Listen(inner())
            return result

        result = sync_run(program(), sync_handlers_preset)
        listen_result = result.value
        assert list(listen_result.log) == [1, 2, 3]
        # Can iterate multiple times
        assert [x for x in listen_result.log] == [1, 2, 3]


class TestAsyncWriterSemantics:
    """Test Writer semantics in AsyncRuntime."""

    @pytest.mark.asyncio
    async def test_async_listen_basic(self) -> None:
        """Basic Listen works in AsyncRuntime."""
        from doeff.cesk.run import async_handlers_preset, async_run

        

        @do
        def inner():
            yield Tell("async_log")
            return "async_result"

        @do
        def program():
            result = yield Listen(inner())
            return result

        result = (await async_run(program(), async_handlers_preset)).value
        assert result.value == "async_result"
        assert list(result.log) == ["async_log"]

    @pytest.mark.asyncio
    @pytest.mark.xfail(reason="SPEC-CESK-003: Handler-based patterns create nested contexts when forwarding. Effects may bypass intermediate handlers, breaking expected composition semantics.")
    async def test_async_listen_plus_safe(self) -> None:
        """Listen + Safe works in AsyncRuntime."""
        from doeff.cesk.run import async_handlers_preset, async_run

        

        @do
        def inner():
            yield Tell("before_fail")
            raise ValueError("async_error")

        @do
        def program():
            result = yield Listen(Safe(inner()))
            return result

        result = (await async_run(program(), async_handlers_preset)).value
        assert result.value.is_err()
        assert list(result.log) == ["before_fail"]

    @pytest.mark.asyncio
    @pytest.mark.xfail(reason="SPEC-CESK-003: Handler-based patterns create nested contexts when forwarding. Effects may bypass intermediate handlers, breaking expected composition semantics.")
    async def test_async_nested_listen(self) -> None:
        """Nested Listen works in AsyncRuntime."""
        from doeff.cesk.run import async_handlers_preset, async_run

        

        @do
        def inner():
            yield Tell("inner")
            return 42

        @do
        def outer():
            yield Tell("outer_start")
            result = yield Listen(inner())
            yield Tell("outer_end")
            return result

        @do
        def program():
            result = yield Listen(outer())
            return result

        result = (await async_run(program(), async_handlers_preset)).value
        assert list(result.log) == ["outer_start", "inner", "outer_end"]
        assert result.value.value == 42
        assert list(result.value.log) == ["inner"]
