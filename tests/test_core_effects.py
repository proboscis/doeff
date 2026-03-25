"""Tests for core effects — Ask, Get, Put, Tell."""

from doeff import do, run as doeff_run, WithHandler, Pure
from doeff_core_effects.effects import Ask, Get, Put, Tell, Try, Slog
from doeff_core_effects.handlers import reader, state, writer, try_handler, slog_handler


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

        with pytest.raises(KeyError, match="missing"):
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


class TestTry:
    def test_try_success(self):
        @do
        def body():
            result = yield Try(Pure(42))
            return result

        from doeff_vm import Ok
        result = doeff_run(WithHandler(try_handler(), body()))
        assert isinstance(result, Ok.__class__) or (hasattr(result, 'is_ok') and result.is_ok())
        assert result.value == 42

    def test_try_failure(self):
        @do
        def failing():
            raise ValueError("boom")
            yield

        @do
        def body():
            result = yield Try(failing())
            return result

        from doeff_vm import Err
        result = doeff_run(WithHandler(try_handler(), body()))
        assert hasattr(result, 'is_err') and result.is_err()
        assert isinstance(result.error, ValueError)

    def test_try_does_not_propagate(self):
        """Try catches errors — they don't propagate."""
        @do
        def failing():
            raise RuntimeError("should be caught")
            yield

        @do
        def body():
            result = yield Try(failing())
            return "safe"

        assert doeff_run(WithHandler(try_handler(), body())) == "safe"


class TestSlog:
    def test_slog_basic(self):
        sh = slog_handler()

        @do
        def body():
            yield Slog("hello")
            yield Slog("event", user="alice", action="login")
            return "done"

        result = doeff_run(WithHandler(sh, body()))
        assert result == "done"
        assert len(sh.log) == 2
        assert sh.log[0] == {"msg": "hello"}
        assert sh.log[1] == {"msg": "event", "user": "alice", "action": "login"}


class TestAwait:
    def test_await_coroutine(self):
        """Await bridges async coroutines into doeff."""
        import asyncio
        from doeff_core_effects import Await, await_handler
        from doeff_core_effects.scheduler import scheduled

        async def async_add(a, b):
            await asyncio.sleep(0.01)
            return a + b

        @do
        def body():
            result = yield Await(async_add(3, 4))
            return result

        result = doeff_run(scheduled(WithHandler(await_handler(), body())))
        assert result == 7

    def test_await_multiple(self):
        """Multiple Awaits in sequence."""
        import asyncio
        from doeff_core_effects import Await, await_handler
        from doeff_core_effects.scheduler import scheduled

        async def fetch(x):
            await asyncio.sleep(0.01)
            return x * 10

        @do
        def body():
            a = yield Await(fetch(1))
            b = yield Await(fetch(2))
            return a + b

        result = doeff_run(scheduled(WithHandler(await_handler(), body())))
        assert result == 30

    def test_await_100_concurrent_tasks(self):
        """100 spawned tasks each awaiting 100ms sleep — must finish in <2s."""
        import asyncio
        import time
        from doeff_core_effects import Await, await_handler
        from doeff_core_effects.scheduler import scheduled, Spawn, Gather

        async def fetch(x):
            await asyncio.sleep(0.1)
            return x

        ah = await_handler()

        @do
        def task(x):
            return (yield Await(fetch(x)))

        @do
        def body():
            tasks = []
            for i in range(100):
                tasks.append((yield Spawn(WithHandler(ah, task(i)))))
            return (yield Gather(*tasks))

        start = time.time()
        result = doeff_run(scheduled(body()))
        elapsed = time.time() - start
        assert result == list(range(100))
        # 100 tasks × 0.1s sleep = should be ~0.1s if concurrent, not 10s
        assert elapsed < 2.0, f"took {elapsed:.1f}s — not concurrent!"


class TestGetExecutionContext:
    def test_get_execution_context(self):
        from doeff import GetExecutionContext

        @do
        def body():
            ctx = yield GetExecutionContext()
            return ctx

        result = doeff_run(body())
        assert isinstance(result, list)
