"""Tests for core effects — Ask, Get, Put, Tell."""

from doeff import do, run as doeff_run, WithHandler
from doeff.program import WithHandler
from doeff_core_effects.effects import Ask, Get, Put, Tell
from doeff_core_effects.handlers import reader, state, writer


class TestReader:
    def test_ask_returns_env_value(self):
        @do
        def body():
            return (yield Ask("name"))

        result = doeff_run(WithHandler(reader(env={"name": "Alice"}), body()))
        assert result == "Alice"

    def test_ask_missing_key_raises(self):
        import pytest

        @do
        def body():
            return (yield Ask("missing"))

        with pytest.raises(RuntimeError):
            doeff_run(WithHandler(reader(env={}), body()))

    def test_ask_multiple_keys(self):
        @do
        def body():
            a = yield Ask("x")
            b = yield Ask("y")
            return a + b

        result = doeff_run(WithHandler(reader(env={"x": 10, "y": 20}), body()))
        assert result == 30


class TestState:
    def test_get_put(self):
        @do
        def body():
            yield Put("count", 0)
            c = yield Get("count")
            yield Put("count", c + 1)
            return (yield Get("count"))

        result = doeff_run(WithHandler(state(), body()))
        assert result == 1

    def test_initial_state(self):
        @do
        def body():
            return (yield Get("x"))

        result = doeff_run(WithHandler(state(initial={"x": 42}), body()))
        assert result == 42

    def test_get_missing_returns_none(self):
        @do
        def body():
            return (yield Get("missing"))

        result = doeff_run(WithHandler(state(), body()))
        assert result is None


class TestWriter:
    def test_tell_collects_messages(self):
        w = writer()

        @do
        def body():
            yield Tell("hello")
            yield Tell("world")
            return "done"

        result = doeff_run(WithHandler(w, body()))
        assert result == "done"
        assert w.log == ["hello", "world"]


class TestComposed:
    def test_reader_and_state_together(self):
        """Multiple handlers composed."""
        @do
        def body():
            base = yield Ask("base")
            yield Put("total", base)
            total = yield Get("total")
            yield Put("total", total + 10)
            return (yield Get("total"))

        prog = WithHandler(
            reader(env={"base": 100}),
            WithHandler(state(), body()),
        )
        assert doeff_run(prog) == 110

    def test_all_three(self):
        """Reader + State + Writer composed."""
        w = writer()

        @do
        def body():
            name = yield Ask("name")
            yield Tell(f"hello {name}")
            yield Put("greeted", True)
            return (yield Get("greeted"))

        prog = WithHandler(
            reader(env={"name": "Bob"}),
            WithHandler(state(), WithHandler(w, body())),
        )
        assert doeff_run(prog) is True
        assert w.log == ["hello Bob"]
