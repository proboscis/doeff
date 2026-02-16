from __future__ import annotations

from pathlib import Path

from doeff import Ask, Gather, Safe, Spawn, default_handlers, do, run


ROOT = Path(__file__).resolve().parents[2]


class TestLazyAskSemaphoreContract:
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

    def test_rust_store_no_mutex_lazy_cache(self) -> None:
        """rust_store.rs must not use Mutex for lazy_cache (use Semaphore instead)."""
        source = (ROOT / "packages" / "doeff-vm" / "src" / "rust_store.rs").read_text()
        assert "Mutex<HashMap<String, LazyCacheEntry>>" not in source, (
            "lazy_cache still uses Mutex. Must use per-key Semaphore(1) "
            "per SPEC-EFF-001 §Concurrency Contract."
        )

    def test_concurrent_ask_not_flagged_as_circular(self) -> None:
        """Task B waiting on same key as Task A must not be a circular dependency error."""

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
        """If lazy evaluation fails, semaphore must be released (try/finally)."""

        @do
        def failing_service():
            raise ValueError("boom")

        @do
        def program():
            result = yield Safe(Ask("service"))
            return result

        result = run(program(), handlers=default_handlers(), env={"service": failing_service()})
        assert result.is_ok()
        safe_result = result.value
        assert safe_result.is_err()
        assert isinstance(safe_result.error, ValueError)

    def test_reader_handler_uses_semaphore_effects(self) -> None:
        """Reader handler must use AcquireSemaphore/ReleaseSemaphore for lazy Ask."""
        reader_py = (ROOT / "doeff" / "effects" / "reader.py").read_text()
        has_py_semaphore = "AcquireSemaphore" in reader_py or "CreateSemaphore" in reader_py

        handler_rs = (ROOT / "packages" / "doeff-vm" / "src" / "handler.rs").read_text()
        has_rs_semaphore = (
            "AcquireSemaphore" in handler_rs
            or "CreateSemaphore" in handler_rs
            or "semaphore" in handler_rs.lower()
        )

        assert has_py_semaphore or has_rs_semaphore, (
            "Neither Python nor Rust reader handler uses semaphore effects. "
            "SPEC-EFF-001 requires per-key Semaphore(1) for lazy Ask coordination."
        )
