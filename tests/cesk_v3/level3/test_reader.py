from __future__ import annotations

from doeff.cesk_v3.level2_algebraic_effects.primitives import WithHandler
from doeff.cesk_v3.level3_core_effects import (
    Ask,
    AskEffect,
    Local,
    LocalEffect,
    reader_handler,
)
from doeff.cesk_v3.run import sync_run
from doeff.do import do
from doeff.program import Program


class TestAskEffect:
    def test_ask_creates_effect(self):
        effect = Ask("key")
        assert isinstance(effect, AskEffect)
        assert effect.key == "key"

    def test_ask_returns_value_from_env(self):
        @do
        def program() -> Program[str]:
            return (yield Ask("name"))

        handler = reader_handler({"name": "Alice"})
        result = sync_run(WithHandler(handler, program()))
        assert result.unwrap() == "Alice"

    def test_ask_returns_none_for_missing_key(self):
        @do
        def program() -> Program[str | None]:
            return (yield Ask("missing"))

        handler = reader_handler({"other": "value"})
        result = sync_run(WithHandler(handler, program()))
        assert result.unwrap() is None

    def test_multiple_asks(self):
        @do
        def program() -> Program[tuple]:
            a = yield Ask("a")
            b = yield Ask("b")
            c = yield Ask("c")
            return (a, b, c)

        handler = reader_handler({"a": 1, "b": 2, "c": 3})
        result = sync_run(WithHandler(handler, program()))
        assert result.unwrap() == (1, 2, 3)


class TestLocalEffect:
    def test_local_creates_effect(self):
        @do
        def sub() -> Program[int]:
            return 42

        effect = Local({"key": "value"}, sub())
        assert isinstance(effect, LocalEffect)
        assert effect.env_update == {"key": "value"}

    def test_local_adds_new_key(self):
        @do
        def sub_program() -> Program[str]:
            return (yield Ask("new_key"))

        @do
        def program() -> Program[str]:
            return (yield Local({"new_key": "new_value"}, sub_program()))

        handler = reader_handler({})
        result = sync_run(WithHandler(handler, program()))
        assert result.unwrap() == "new_value"

    def test_local_overrides_existing_key(self):
        @do
        def sub_program() -> Program[str]:
            return (yield Ask("key"))

        @do
        def program() -> Program[str]:
            return (yield Local({"key": "overridden"}, sub_program()))

        handler = reader_handler({"key": "original"})
        result = sync_run(WithHandler(handler, program()))
        assert result.unwrap() == "overridden"

    def test_local_preserves_other_keys(self):
        @do
        def sub_program() -> Program[tuple]:
            a = yield Ask("a")
            b = yield Ask("b")
            return (a, b)

        @do
        def program() -> Program[tuple]:
            return (yield Local({"b": "modified"}, sub_program()))

        handler = reader_handler({"a": "original_a", "b": "original_b"})
        result = sync_run(WithHandler(handler, program()))
        assert result.unwrap() == ("original_a", "modified")

    def test_local_is_scoped(self):
        @do
        def sub_program() -> Program[str]:
            return (yield Ask("key"))

        @do
        def program() -> Program[tuple]:
            before = yield Ask("key")
            inside = yield Local({"key": "local_value"}, sub_program())
            after = yield Ask("key")
            return (before, inside, after)

        handler = reader_handler({"key": "outer_value"})
        result = sync_run(WithHandler(handler, program()))
        before, inside, after = result.unwrap()
        assert before == "outer_value"
        assert inside == "local_value"
        assert after == "outer_value"

    def test_nested_locals(self):
        @do
        def inner() -> Program[int]:
            return (yield Ask("depth"))

        @do
        def middle() -> Program[int]:
            return (yield Local({"depth": 2}, inner()))

        @do
        def outer() -> Program[tuple]:
            d1 = yield Ask("depth")
            d2 = yield Local({"depth": 1}, middle())
            d3 = yield Ask("depth")
            return (d1, d2, d3)

        handler = reader_handler({"depth": 0})
        result = sync_run(WithHandler(handler, outer()))
        assert result.unwrap() == (0, 2, 0)

    def test_local_with_empty_update(self):
        @do
        def sub_program() -> Program[str]:
            return (yield Ask("key"))

        @do
        def program() -> Program[str]:
            return (yield Local({}, sub_program()))

        handler = reader_handler({"key": "value"})
        result = sync_run(WithHandler(handler, program()))
        assert result.unwrap() == "value"


class TestReaderHandlerIntegration:
    def test_reader_with_computation(self):
        @do
        def compute_greeting() -> Program[str]:
            name = yield Ask("name")
            greeting = yield Ask("greeting")
            return f"{greeting}, {name}!"

        handler = reader_handler({"name": "World", "greeting": "Hello"})
        result = sync_run(WithHandler(handler, compute_greeting()))
        assert result.unwrap() == "Hello, World!"

    def test_local_in_loop_pattern(self):
        @do
        def process_item() -> Program[str]:
            item = yield Ask("current_item")
            prefix = yield Ask("prefix")
            return f"{prefix}:{item}"

        @do
        def program() -> Program[list]:
            results = []
            for item in ["a", "b", "c"]:
                result = yield Local({"current_item": item}, process_item())
                results.append(result)
            return results

        handler = reader_handler({"prefix": "item"})
        result = sync_run(WithHandler(handler, program()))
        assert result.unwrap() == ["item:a", "item:b", "item:c"]
