from __future__ import annotations

from pathlib import Path

from doeff import Ask, Gather, Safe, Spawn, default_handlers, do, run
from doeff.handlers import lazy_ask, reader, scheduler, state

ROOT = Path(__file__).resolve().parents[2]


class TestLazyAskSemaphoreContract:
    @staticmethod
    def _perform_enter_count(trace: list[dict[str, object]]) -> int:
        return sum(
            1
            for event in trace
            if event.get("event") == "enter" and event.get("mode") == "HandleYield(Perform)"
        )

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

    def test_non_lazy_ask_passthrough(self) -> None:
        @do
        def program():
            return (yield Ask("key"))

        result = run(program(), handlers=default_handlers(), env={"key": "plain_value"})
        assert result.is_ok()
        assert result.value == "plain_value"

    def test_reader_no_semaphore_dependency(self) -> None:
        @do
        def program():
            return (yield Ask("key"))

        result = run(program(), handlers=[reader], env={"key": "value"})
        assert result.is_ok()
        assert result.value == "value"

    def test_ordering_independence(self) -> None:
        @do
        def service_program():
            if False:
                yield
            return 42

        @do
        def program():
            return (yield Ask("service"))

        orderings = [
            [state, reader, scheduler, lazy_ask],
            [scheduler, state, reader, lazy_ask],
            [reader, state, scheduler, lazy_ask],
        ]
        for handlers in orderings:
            result = run(program(), handlers=handlers, env={"service": service_program()})
            assert result.is_ok()
            assert result.value == 42

    def test_lazy_ask_dispatches_semaphore_effects_in_trace(self) -> None:
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
        plain_result = run(
            program(),
            handlers=default_handlers(),
            env={"service": 42},
            trace=True,
        )
        assert result.is_ok()
        assert plain_result.is_ok()
        assert result.value == 42

        lazy_performs = self._perform_enter_count(result.trace)
        plain_performs = self._perform_enter_count(plain_result.trace)
        assert lazy_performs >= plain_performs + 3, (
            "Lazy Ask should perform at least Create/Acquire/Release semaphore effects. "
            f"lazy_performs={lazy_performs}, plain_performs={plain_performs}"
        )

    def test_no_os_lock_for_lazy_cache(self) -> None:
        source = (ROOT / "packages" / "doeff-vm" / "src" / "rust_store.rs").read_text()

        assert "Mutex<HashMap<String, LazyCacheEntry>>" not in source, (
            "lazy_cache still uses Mutex. Must use semaphore effects."
        )
        assert "RwLock<HashMap<String, LazyCacheEntry>>" not in source, (
            "lazy_cache uses RwLock. RwLock is still an OS-level lock. "
            "Must use cooperative semaphore effects per SPEC-EFF-001."
        )
        assert "lazy_cache:" not in source
        assert "lazy_semaphores:" not in source

    def test_concurrent_ask_not_flagged_as_circular(self) -> None:
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
        @do
        def service_a():
            if False:
                yield
            return "a"

        @do
        def service_b():
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
        plain_result = run(
            program(),
            handlers=default_handlers(),
            env={"svc_a": "a", "svc_b": "b"},
            trace=True,
        )
        assert result.is_ok()
        assert plain_result.is_ok()
        assert result.value == ("a", "b")

        lazy_performs = self._perform_enter_count(result.trace)
        plain_performs = self._perform_enter_count(plain_result.trace)
        assert lazy_performs >= plain_performs + 6, (
            "Resolving two lazy keys should perform additional semaphore effects per key. "
            f"lazy_performs={lazy_performs}, plain_performs={plain_performs}"
        )
