"""Tests for core effects — Ask, Get, Put, Tell."""

from doeff_core_effects.effects import Ask, Get, Put, Slog, Tell, Try
from doeff_core_effects.handlers import (
    reader,
    slog_handler,
    slog_log,
    state,
    try_handler,
    writer,
    writer_log,
)

from doeff import Pure, do
from doeff import run as doeff_run


class TestReader:
    def test_ask_returns_env_value(self):
        @do
        def body():
            return (yield Ask("name"))

        result = doeff_run(reader(env={"name": "Alice"})(body()))
        assert result == "Alice"

    def test_ask_missing_key_raises(self):
        import pytest

        @do
        def body():
            return (yield Ask("missing"))

        with pytest.raises(KeyError, match="missing"):
            doeff_run(reader(env={})(body()))

    def test_ask_multiple_keys(self):
        @do
        def body():
            a = yield Ask("x")
            b = yield Ask("y")
            return a + b

        result = doeff_run(reader(env={"x": 10, "y": 20})(body()))
        assert result == 30


class TestState:
    def test_get_put(self):
        @do
        def body():
            yield Put("count", 0)
            c = yield Get("count")
            yield Put("count", c + 1)
            return (yield Get("count"))

        result = doeff_run(state()(body()))
        assert result == 1

    def test_initial_state(self):
        @do
        def body():
            return (yield Get("x"))

        result = doeff_run(state(initial={"x": 42})(body()))
        assert result == 42

    def test_get_missing_returns_none(self):
        @do
        def body():
            return (yield Get("missing"))

        result = doeff_run(state()(body()))
        assert result is None


class TestWriter:
    def test_tell_collects_messages(self):
        @do
        def body():
            yield Tell("hello")
            yield Tell("world")
            return (yield writer_log())

        result = doeff_run(state()(writer(body())))
        assert result == ["hello", "world"]


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

        prog = reader(env={"base": 100})(state()(body()))
        assert doeff_run(prog) == 110

    def test_all_three(self):
        """Reader + State + Writer composed."""

        @do
        def body():
            name = yield Ask("name")
            yield Tell(f"hello {name}")
            yield Put("greeted", True)
            greeted = yield Get("greeted")
            log = yield writer_log()
            return (greeted, log)

        prog = reader(env={"name": "Bob"})(state()(writer(body())))
        result = doeff_run(prog)
        assert result == (True, ["hello Bob"])


class TestTry:
    def test_try_success(self):
        @do
        def body():
            result = yield Try(Pure(42))
            return result

        from doeff_vm import Ok
        result = doeff_run(try_handler(body()))
        assert isinstance(result, Ok.__class__) or (hasattr(result, "is_ok") and result.is_ok())
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

        result = doeff_run(try_handler(body()))
        assert hasattr(result, "is_err")
        assert result.is_err()
        assert isinstance(result.error, ValueError)

    def test_try_does_not_propagate(self):
        """Try catches errors — they don't propagate."""
        @do
        def failing():
            raise RuntimeError("should be caught")
            yield

        @do
        def body():
            yield Try(failing())
            return "safe"

        assert doeff_run(try_handler(body())) == "safe"


class TestSlog:
    def test_slog_basic(self):
        @do
        def body():
            yield Slog("hello")
            yield Slog("event", user="alice", action="login")
            log = yield slog_log()
            return log

        result = doeff_run(state()(slog_handler(body())))
        assert len(result) == 2
        assert result[0] == {"msg": "hello"}
        assert result[1] == {"msg": "event", "user": "alice", "action": "login"}


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

        result = doeff_run(scheduled(await_handler()(body())))
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

        result = doeff_run(scheduled(await_handler()(body())))
        assert result == 30

    def test_await_100_concurrent_tasks(self):
        """100 spawned tasks each awaiting 100ms sleep — must finish in <2s."""
        import asyncio
        import time

        from doeff_core_effects import Await, await_handler
        from doeff_core_effects.scheduler import Gather, Spawn, scheduled

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
                tasks.append((yield Spawn(ah(task(i)))))
            return (yield Gather(*tasks))

        start = time.time()
        result = doeff_run(scheduled(body()))
        elapsed = time.time() - start
        assert result == list(range(100))
        # 100 tasks x 0.1s sleep = should be ~0.1s if concurrent, not 10s
        assert elapsed < 2.0, f"took {elapsed:.1f}s — not concurrent!"

    def test_await_base_exception_fails_promise_not_hang(self):
        """#494: a coroutine raising a BaseException (asyncio.CancelledError)
        must fail the Await promptly — not park the scheduler forever."""
        import asyncio
        import threading

        from doeff_core_effects import Await, await_handler
        from doeff_core_effects.scheduler import scheduled

        async def evil():
            raise asyncio.CancelledError

        @do
        def body():
            v = yield Await(evil())
            return v

        # Run in a worker thread guarded by a timeout so a regression
        # (permanent hang on external_queue.get) fails instead of wedging
        # the test session (pattern: test_scheduler._run_scheduled_with_timeout).
        error: dict[str, BaseException] = {}

        def worker() -> None:
            try:
                doeff_run(scheduled(await_handler()(body())))
            except BaseException as exc:  # re-checked below
                error["value"] = exc

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join(timeout=5.0)
        assert not thread.is_alive(), (
            "scheduler hung: BaseException in awaited coroutine never resolved "
            "its ExternalPromise (#494)"
        )
        assert isinstance(error.get("value"), asyncio.CancelledError)

    def test_await_handler_instances_share_one_loop_thread(self):
        """#498: sequential runs with fresh await_handler() instances must
        share one process-global loop thread, not leak one thread per run."""
        import asyncio
        import threading

        from doeff_core_effects import Await, await_handler
        from doeff_core_effects.scheduler import scheduled

        @do
        def body():
            yield Await(asyncio.sleep(0))
            return 1

        before = threading.active_count()
        for _ in range(20):
            assert doeff_run(scheduled(await_handler()(body()))) == 1
        after = threading.active_count()
        # At most +1: the shared bridge loop thread (0 if an earlier test
        # already created it).
        assert after - before <= 1, (
            f"leaked {after - before} threads across 20 runs "
            "(one background loop thread per await_handler instance, #498)"
        )


class TestGetExecutionContext:
    def test_get_execution_context(self):
        from doeff import GetExecutionContext

        @do
        def body():
            ctx = yield GetExecutionContext()
            return ctx

        result = doeff_run(body())
        assert isinstance(result, list)
