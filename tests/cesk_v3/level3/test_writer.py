from __future__ import annotations

import pytest

from doeff.cesk_v3.level2_algebraic_effects.frames import EffectBase
from doeff.cesk_v3.level2_algebraic_effects.primitives import Forward, Resume, WithHandler
from doeff.cesk_v3.level3_core_effects import (
    Get,
    Listen,
    Put,
    Tell,
    WriterListenEffect,
    WriterTellEffect,
    state_handler,
    writer_handler,
)
from doeff.cesk_v3.run import sync_run
from doeff.do import do
from doeff.program import Program


class TestWriterEffectTypes:
    def test_tell_creates_effect(self):
        effect = Tell("hello")
        assert isinstance(effect, WriterTellEffect)
        assert effect.message == "hello"

    def test_listen_creates_effect(self):
        effect = Listen()
        assert isinstance(effect, WriterListenEffect)

    def test_tell_with_different_types(self):
        assert Tell(42).message == 42
        assert Tell(["a", "b"]).message == ["a", "b"]
        assert Tell({"key": "value"}).message == {"key": "value"}
        assert Tell(None).message is None


class TestWriterHandler:
    def test_single_tell(self):
        @do
        def program() -> Program[None]:
            yield Tell("hello")
            return None

        handler, messages = writer_handler()
        result = sync_run(WithHandler(handler, program()))
        assert result.is_ok
        assert messages == ["hello"]

    def test_multiple_tells(self):
        @do
        def program() -> Program[None]:
            yield Tell("first")
            yield Tell("second")
            yield Tell("third")
            return None

        handler, messages = writer_handler()
        result = sync_run(WithHandler(handler, program()))
        assert result.is_ok
        assert messages == ["first", "second", "third"]

    def test_listen_returns_accumulated_messages(self):
        @do
        def program() -> Program[list]:
            yield Tell("a")
            yield Tell("b")
            result = yield Listen()
            return result

        handler, _ = writer_handler()
        result = sync_run(WithHandler(handler, program()))
        assert result.unwrap() == ["a", "b"]

    def test_listen_returns_copy_not_internal_reference(self):
        @do
        def program() -> Program[tuple]:
            yield Tell("original")
            first_listen_result = yield Listen()
            first_listen_result.append("mutated_by_caller")
            second_listen_result = yield Listen()
            return (first_listen_result, second_listen_result)

        handler, _ = writer_handler()
        result = sync_run(WithHandler(handler, program()))
        mutated_copy, fresh_copy = result.unwrap()
        assert mutated_copy == ["original", "mutated_by_caller"]
        assert fresh_copy == ["original"]

    def test_tell_after_listen(self):
        @do
        def program() -> Program[list]:
            yield Tell("before")
            _ = yield Listen()
            yield Tell("after")
            return (yield Listen())

        handler, messages = writer_handler()
        result = sync_run(WithHandler(handler, program()))
        assert result.unwrap() == ["before", "after"]
        assert messages == ["before", "after"]

    def test_empty_listen(self):
        @do
        def program() -> Program[list]:
            return (yield Listen())

        handler, _ = writer_handler()
        result = sync_run(WithHandler(handler, program()))
        assert result.unwrap() == []

    def test_handler_returns_messages_list(self):
        @do
        def program() -> Program[str]:
            yield Tell("log1")
            yield Tell("log2")
            return "done"

        handler, messages = writer_handler()
        result = sync_run(WithHandler(handler, program()))
        assert result.unwrap() == "done"
        assert messages == ["log1", "log2"]


class TestWriterWithOtherEffects:
    def test_writer_with_state(self):
        @do
        def program() -> Program[int]:
            yield Tell("initializing")
            yield Put("counter", 0)
            yield Tell("counter set to 0")
            c = yield Get("counter")
            yield Put("counter", c + 1)
            yield Tell("counter incremented")
            return (yield Get("counter"))

        writer_h, messages = writer_handler()
        result = sync_run(
            WithHandler(state_handler(), WithHandler(writer_h, program()))
        )
        assert result.unwrap() == 1
        assert messages == ["initializing", "counter set to 0", "counter incremented"]

    def test_writer_forwards_unknown_effects(self):
        @do
        def forwarding_handler(effect: EffectBase) -> Program[int]:
            forwarded = yield Forward(effect)
            return (yield Resume(forwarded))

        @do
        def program() -> Program[list]:
            yield Tell("message")
            return (yield Listen())

        writer_h, _ = writer_handler()
        result = sync_run(
            WithHandler(forwarding_handler, WithHandler(writer_h, program()))
        )
        assert result.unwrap() == ["message"]


class TestWriterEdgeCases:
    def test_tell_with_complex_objects(self):
        class LogEntry:
            def __init__(self, level: str, msg: str):
                self.level = level
                self.msg = msg

            def __eq__(self, other):
                return self.level == other.level and self.msg == other.msg

        @do
        def program() -> Program[list]:
            yield Tell(LogEntry("INFO", "started"))
            yield Tell(LogEntry("ERROR", "failed"))
            return (yield Listen())

        handler, _ = writer_handler()
        result = sync_run(WithHandler(handler, program()))
        logs = result.unwrap()
        assert len(logs) == 2
        assert logs[0] == LogEntry("INFO", "started")
        assert logs[1] == LogEntry("ERROR", "failed")

    def test_independent_writer_handlers(self):
        @do
        def program_a() -> Program[list]:
            yield Tell("a1")
            yield Tell("a2")
            return (yield Listen())

        @do
        def program_b() -> Program[list]:
            yield Tell("b1")
            return (yield Listen())

        handler_a, messages_a = writer_handler()
        handler_b, messages_b = writer_handler()

        result_a = sync_run(WithHandler(handler_a, program_a()))
        result_b = sync_run(WithHandler(handler_b, program_b()))

        assert result_a.unwrap() == ["a1", "a2"]
        assert result_b.unwrap() == ["b1"]
        assert messages_a == ["a1", "a2"]
        assert messages_b == ["b1"]
