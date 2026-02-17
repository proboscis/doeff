from __future__ import annotations

from pathlib import Path

import pytest

from doeff import (
    Ask,
    Gather,
    Safe,
    Spawn,
    async_run,
    default_async_handlers,
    default_handlers,
    do,
    run,
)
from doeff.handlers import lazy_ask, reader, scheduler, state

ROOT = Path(__file__).resolve().parents[2]


class TestLazyAskSemaphoreContract:
    @pytest.mark.asyncio
    async def test_lazy_ask_delegates_to_reader(self) -> None:
        """LazyAsk delegates Ask to Reader, then evaluates lazy values."""

        @do
        def service():
            return 42

        @do
        def program():
            return (yield Ask("svc"))

        result = await async_run(
            program(),
            handlers=default_async_handlers(),
            env={"svc": service()},
        )
        assert result.is_ok()
        assert result.value == 42

    def test_lazy_ask_caches(self) -> None:
        calls = {"service": 0}

        @do
        def service_program():
            calls["service"] += 1
            if False:
                yield
            return 42

        @do
        def program():
            first = yield Ask("service")
            second = yield Ask("service")
            return (first, second)

        result = run(program(), handlers=default_handlers(), env={"service": service_program()})
        assert result.is_ok()
        assert result.value == (42, 42)
        assert calls["service"] == 1

    def test_concurrent_lazy_ask_single_evaluation(self) -> None:
        """Two spawned tasks Ask-ing the same lazy key must evaluate only once."""
        calls = {"service": 0}

        @do
        def service_program():
            calls["service"] += 1
            if False:
                yield
            return 42

        @do
        def child():
            return (yield Ask("service"))

        @do
        def program():
            t1 = yield Spawn(child())
            t2 = yield Spawn(child())
            return (yield Gather(t1, t2))

        result = run(program(), handlers=default_handlers(), env={"service": service_program()})
        assert result.is_ok()
        assert result.value == [42, 42]
        assert calls["service"] == 1

    def test_lazy_ask_dispatches_semaphore_effects_in_trace(self) -> None:
        """trace=True should produce a trace while lazy Ask resolves correctly."""

        @do
        def service_program():
            if False:
                yield
            return 42

        @do
        def program():
            return (yield Ask("service"))

        result = run(
            program(),
            handlers=default_handlers(),
            env={"service": service_program()},
            trace=True,
        )
        assert result.is_ok()
        assert result.value == 42
        assert result.trace is not None
        assert len(result.trace) > 0

    def test_non_lazy_ask_passthrough(self) -> None:
        @do
        def program():
            return (yield Ask("key"))

        result = run(program(), handlers=default_handlers(), env={"key": "plain_value"})
        assert result.is_ok()
        assert result.value == "plain_value"

    def test_no_os_lock_for_lazy_cache(self) -> None:
        """rust_store.rs must not use Mutex/RwLock for lazy_cache."""
        source = (ROOT / "packages" / "doeff-vm" / "src" / "rust_store.rs").read_text()

        assert "Mutex<HashMap<String, LazyCacheEntry>>" not in source, (
            "lazy_cache still uses Mutex. Must use semaphore effects."
        )
        assert "RwLock<HashMap<String, LazyCacheEntry>>" not in source, (
            "lazy_cache uses RwLock. RwLock is still an OS-level lock. "
            "Must use cooperative semaphore effects per SPEC-EFF-001."
        )

    def test_concurrent_ask_not_flagged_as_circular(self) -> None:
        """Waiting tasks on same key must not be treated as circular dependency errors."""

        @do
        def slow_service():
            if False:
                yield
            return "resolved"

        @do
        def child():
            return (yield Ask("slow"))

        @do
        def program():
            t1 = yield Spawn(child())
            t2 = yield Spawn(child())
            t3 = yield Spawn(child())
            return (yield Gather(t1, t2, t3))

        result = run(program(), handlers=default_handlers(), env={"slow": slow_service()})
        assert result.is_ok()
        assert result.value == ["resolved", "resolved", "resolved"]

    def test_lazy_ask_failure_releases_semaphore_for_retry(self) -> None:
        """If lazy evaluation fails, semaphore must be released (try/finally semantics)."""

        @do
        def failing_service():
            raise ValueError("boom")

        @do
        def program():
            return (yield Safe(Ask("service")))

        result = run(program(), handlers=default_handlers(), env={"service": failing_service()})
        assert result.is_ok()
        safe_result = result.value
        assert safe_result.is_err()
        assert isinstance(safe_result.error, ValueError)

    def test_lazy_ask_creates_semaphore_per_key_in_trace(self) -> None:
        """Distinct lazy keys should evaluate independently and exactly once."""
        calls = {"a": 0, "b": 0}

        @do
        def service_a():
            calls["a"] += 1
            if False:
                yield
            return "a"

        @do
        def service_b():
            calls["b"] += 1
            if False:
                yield
            return "b"

        @do
        def program():
            a = yield Ask("svc_a")
            b = yield Ask("svc_b")
            return (a, b)

        result = run(
            program(),
            handlers=default_handlers(),
            env={"svc_a": service_a(), "svc_b": service_b()},
            trace=True,
        )
        assert result.is_ok()
        assert result.value == ("a", "b")
        assert calls == {"a": 1, "b": 1}

    def test_reader_no_semaphore_dependency(self) -> None:
        """Reader must remain pure lookup and work without scheduler."""

        @do
        def program():
            return (yield Ask("key"))

        result = run(program(), handlers=[reader], env={"key": "value"})
        assert result.is_ok()
        assert result.value == "value"

    def test_reader_pure_no_semaphore(self) -> None:
        """Reader alone handles Ask without scheduler."""

        @do
        def program():
            return (yield Ask("key"))

        result = run(program(), handlers=[reader], env={"key": "value"})
        assert result.is_ok()
        assert result.value == "value"

    def test_ordering_independence(self) -> None:
        @do
        def service():
            if False:
                yield
            return 42

        @do
        def program():
            return (yield Ask("svc"))

        for ordering in (
            [state, reader, scheduler, lazy_ask],
            [scheduler, state, reader, lazy_ask],
            [reader, state, scheduler, lazy_ask],
        ):
            result = run(program(), handlers=ordering, env={"svc": service()})
            assert result.is_ok()
            assert result.value == 42
