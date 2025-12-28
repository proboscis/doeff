"""Tests for unjoined spawned task warnings.

These tests verify that:
1. Unjoined spawned tasks produce a helpful warning at program end
2. Warning includes actionable guidance (join, Safe, Recover patterns)
3. Python's "Future exception was never retrieved" warning is suppressed
4. fire_and_forget=True parameter suppresses the unjoined warning
5. Warning shows count of unjoined tasks
"""

import logging
import warnings
from typing import Any

import pytest

from doeff import (
    EffectGenerator,
    Fail,
    ProgramInterpreter,
    Recover,
    Safe,
    Spawn,
    do,
)


@pytest.fixture
def caplog_warning(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """Set up log capture for warning level on doeff.handlers."""
    caplog.set_level(logging.WARNING, logger="doeff.handlers")
    return caplog


class TestUnjoinedTaskWarnings:
    """Tests for unjoined task warning functionality."""

    def test_unjoined_task_produces_warning(
        self, caplog_warning: pytest.LogCaptureFixture
    ) -> None:
        """Unjoined spawned tasks produce a helpful warning at program end."""
        engine = ProgramInterpreter()

        @do
        def worker() -> EffectGenerator[int]:
            return 42

        @do
        def program() -> EffectGenerator[None]:
            # Spawn but never join
            yield Spawn(worker(), preferred_backend="thread")
            return None

        result = engine.run(program())

        assert result.is_ok
        # Check that warning was logged
        assert len(caplog_warning.records) >= 1
        warning_messages = [r.message for r in caplog_warning.records]
        assert any("spawned task(s) were not joined" in msg for msg in warning_messages)

    def test_warning_includes_actionable_guidance(
        self, caplog_warning: pytest.LogCaptureFixture
    ) -> None:
        """Warning includes actionable guidance (join, Safe, Recover patterns)."""
        engine = ProgramInterpreter()

        @do
        def worker() -> EffectGenerator[int]:
            return 42

        @do
        def program() -> EffectGenerator[None]:
            yield Spawn(worker(), preferred_backend="thread")
            return None

        engine.run(program())

        warning_text = "\n".join(r.message for r in caplog_warning.records)
        # Check for join pattern
        assert "yield task.join()" in warning_text
        # Check for Safe pattern
        assert "yield Safe(task.join())" in warning_text
        # Check for Recover pattern
        assert "yield Recover(task.join(), x)" in warning_text
        # Check for fire_and_forget guidance
        assert "fire_and_forget=True" in warning_text

    def test_warning_shows_count_of_unjoined_tasks(
        self, caplog_warning: pytest.LogCaptureFixture
    ) -> None:
        """Warning shows count of unjoined tasks."""
        engine = ProgramInterpreter()

        @do
        def worker(index: int) -> EffectGenerator[int]:
            return index

        @do
        def program() -> EffectGenerator[None]:
            # Spawn 3 tasks without joining
            yield Spawn(worker(1), preferred_backend="thread")
            yield Spawn(worker(2), preferred_backend="thread")
            yield Spawn(worker(3), preferred_backend="thread")
            return None

        engine.run(program())

        warning_messages = [r.message for r in caplog_warning.records]
        assert any("3 spawned task(s) were not joined" in msg for msg in warning_messages)

    def test_joined_tasks_produce_no_warning(
        self, caplog_warning: pytest.LogCaptureFixture
    ) -> None:
        """Joined tasks produce no warning."""
        engine = ProgramInterpreter()

        @do
        def worker() -> EffectGenerator[int]:
            return 42

        @do
        def program() -> EffectGenerator[int]:
            task = yield Spawn(worker(), preferred_backend="thread")
            return (yield task.join())

        result = engine.run(program())

        assert result.is_ok
        assert result.value == 42
        # No warning should be logged
        warning_messages = [r.message for r in caplog_warning.records]
        assert not any(
            "spawned task(s) were not joined" in msg for msg in warning_messages
        )

    def test_fire_and_forget_produces_no_warning(
        self, caplog_warning: pytest.LogCaptureFixture
    ) -> None:
        """fire_and_forget=True produces no warning."""
        engine = ProgramInterpreter()

        @do
        def worker() -> EffectGenerator[int]:
            return 42

        @do
        def program() -> EffectGenerator[None]:
            # Spawn with fire_and_forget - should not warn
            yield Spawn(worker(), preferred_backend="thread", fire_and_forget=True)
            return None

        result = engine.run(program())

        assert result.is_ok
        # No warning should be logged
        warning_messages = [r.message for r in caplog_warning.records]
        assert not any(
            "spawned task(s) were not joined" in msg for msg in warning_messages
        )

    def test_mix_of_joined_and_unjoined_warns_about_unjoined(
        self, caplog_warning: pytest.LogCaptureFixture
    ) -> None:
        """Mix of joined/unjoined only warns about unjoined."""
        engine = ProgramInterpreter()

        @do
        def worker(index: int) -> EffectGenerator[int]:
            return index

        @do
        def program() -> EffectGenerator[int]:
            task1 = yield Spawn(worker(1), preferred_backend="thread")
            task2 = yield Spawn(worker(2), preferred_backend="thread")
            yield Spawn(worker(3), preferred_backend="thread")  # Not joined
            yield Spawn(worker(4), preferred_backend="thread")  # Not joined
            # Join only first two
            result1 = yield task1.join()
            result2 = yield task2.join()
            return result1 + result2

        result = engine.run(program())

        assert result.is_ok
        assert result.value == 3
        # Warning should mention 2 unjoined tasks
        warning_messages = [r.message for r in caplog_warning.records]
        assert any("2 spawned task(s) were not joined" in msg for msg in warning_messages)

    def test_fire_and_forget_mixed_with_regular_spawn(
        self, caplog_warning: pytest.LogCaptureFixture
    ) -> None:
        """Mix of fire_and_forget and regular spawn handles correctly."""
        engine = ProgramInterpreter()

        @do
        def worker(index: int) -> EffectGenerator[int]:
            return index

        @do
        def program() -> EffectGenerator[int]:
            # Two fire_and_forget (should not count as unjoined)
            yield Spawn(worker(1), preferred_backend="thread", fire_and_forget=True)
            yield Spawn(worker(2), preferred_backend="thread", fire_and_forget=True)
            # One regular spawn that's joined
            task = yield Spawn(worker(3), preferred_backend="thread")
            # One regular spawn that's not joined
            yield Spawn(worker(4), preferred_backend="thread")
            return (yield task.join())

        result = engine.run(program())

        assert result.is_ok
        assert result.value == 3
        # Warning should mention only 1 unjoined task (not the fire_and_forget ones)
        warning_messages = [r.message for r in caplog_warning.records]
        assert any("1 spawned task(s) were not joined" in msg for msg in warning_messages)


class TestFutureExceptionSuppression:
    """Tests for suppression of Python's 'Future exception was never retrieved' warning."""

    def test_no_future_exception_warning_thread_backend(
        self, caplog_warning: pytest.LogCaptureFixture
    ) -> None:
        """No Python 'Future exception' warning for failed unjoined thread tasks."""
        engine = ProgramInterpreter()

        @do
        def failing_worker() -> EffectGenerator[int]:
            yield Fail(RuntimeError("boom"))
            return 0

        @do
        def program() -> EffectGenerator[None]:
            # Spawn failing task but don't join
            yield Spawn(failing_worker(), preferred_backend="thread")
            return None

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = engine.run(program())

            # Filter for Future-related warnings
            future_warnings = [
                warning for warning in w
                if "Future" in str(warning.message) or "exception" in str(warning.message).lower()
            ]

        assert result.is_ok
        # Should not have Python's Future exception warning
        assert len(future_warnings) == 0

    def test_no_future_exception_warning_process_backend(
        self, caplog_warning: pytest.LogCaptureFixture
    ) -> None:
        """No Python 'Future exception' warning for failed unjoined process tasks."""
        engine = ProgramInterpreter(spawn_process_max_workers=2)

        @do
        def failing_worker() -> EffectGenerator[int]:
            yield Fail(RuntimeError("boom"))
            return 0

        @do
        def program() -> EffectGenerator[None]:
            # Spawn failing task but don't join
            yield Spawn(failing_worker(), preferred_backend="process")
            return None

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = engine.run(program())

            # Filter for Future-related warnings
            future_warnings = [
                warning for warning in w
                if "Future" in str(warning.message) or "exception" in str(warning.message).lower()
            ]

        assert result.is_ok
        # Should not have Python's Future exception warning
        assert len(future_warnings) == 0

    def test_multiple_failed_unjoined_tasks_no_spam(
        self, caplog_warning: pytest.LogCaptureFixture
    ) -> None:
        """Multiple failed unjoined tasks don't produce spam warnings."""
        engine = ProgramInterpreter()

        @do
        def failing_worker(index: int) -> EffectGenerator[int]:
            yield Fail(RuntimeError(f"boom {index}"))
            return 0

        @do
        def program() -> EffectGenerator[None]:
            # Spawn multiple failing tasks without joining
            for i in range(5):
                yield Spawn(failing_worker(i), preferred_backend="thread")
            return None

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = engine.run(program())

            # Filter for Future-related warnings
            future_warnings = [
                warning for warning in w
                if "Future" in str(warning.message) or "exception" in str(warning.message).lower()
            ]

        assert result.is_ok
        # Should not have Python's Future exception warnings
        assert len(future_warnings) == 0

        # Should have exactly one unjoined task warning
        warning_messages = [r.message for r in caplog_warning.records]
        unjoined_warnings = [
            msg for msg in warning_messages if "spawned task(s) were not joined" in msg
        ]
        assert len(unjoined_warnings) == 1
        assert "5 spawned task(s) were not joined" in unjoined_warnings[0]


class TestTaskJoinPatterns:
    """Tests for proper handling of various join patterns."""

    def test_safe_join_counts_as_joined(
        self, caplog_warning: pytest.LogCaptureFixture
    ) -> None:
        """Using Safe(task.join()) counts as joining the task."""
        engine = ProgramInterpreter()

        @do
        def failing_worker() -> EffectGenerator[int]:
            yield Fail(RuntimeError("boom"))
            return 0

        @do
        def program() -> EffectGenerator[Any]:
            task = yield Spawn(failing_worker(), preferred_backend="thread")
            # Use Safe to join - should count as joined
            result = yield Safe(task.join())
            return result

        result = engine.run(program())

        assert result.is_ok
        # Result should be Err since worker failed
        assert result.value.is_err

        # No unjoined warning should be logged
        warning_messages = [r.message for r in caplog_warning.records]
        assert not any(
            "spawned task(s) were not joined" in msg for msg in warning_messages
        )

    def test_recover_join_counts_as_joined(
        self, caplog_warning: pytest.LogCaptureFixture
    ) -> None:
        """Using Recover(task.join(), fallback) counts as joining the task."""
        engine = ProgramInterpreter()

        @do
        def failing_worker() -> EffectGenerator[int]:
            yield Fail(RuntimeError("boom"))
            return 0

        @do
        def program() -> EffectGenerator[int]:
            task = yield Spawn(failing_worker(), preferred_backend="thread")
            # Use Recover to join - should count as joined
            return (yield Recover(task.join(), fallback=42))

        result = engine.run(program())

        assert result.is_ok
        assert result.value == 42  # Got fallback value

        # No unjoined warning should be logged
        warning_messages = [r.message for r in caplog_warning.records]
        assert not any(
            "spawned task(s) were not joined" in msg for msg in warning_messages
        )

    def test_multiple_joins_of_same_task(
        self, caplog_warning: pytest.LogCaptureFixture
    ) -> None:
        """Joining the same task multiple times works correctly."""
        engine = ProgramInterpreter()

        @do
        def worker() -> EffectGenerator[int]:
            return 42

        @do
        def program() -> EffectGenerator[tuple[int, int, int]]:
            task = yield Spawn(worker(), preferred_backend="thread")
            # Join same task multiple times
            r1 = yield task.join()
            r2 = yield task.join()
            r3 = yield task.join()
            return r1, r2, r3

        result = engine.run(program())

        assert result.is_ok
        assert result.value == (42, 42, 42)

        # No unjoined warning should be logged
        warning_messages = [r.message for r in caplog_warning.records]
        assert not any(
            "spawned task(s) were not joined" in msg for msg in warning_messages
        )


class TestSpawnHandlerMethods:
    """Tests for SpawnEffectHandler helper methods."""

    def test_get_unjoined_tasks_returns_empty_when_all_joined(self) -> None:
        """get_unjoined_tasks returns empty list when all tasks joined."""
        engine = ProgramInterpreter()

        @do
        def worker(i: int) -> EffectGenerator[int]:
            return i

        @do
        def program() -> EffectGenerator[list[int]]:
            tasks = []
            for i in range(3):
                tasks.append((yield Spawn(worker(i), preferred_backend="thread")))
            results = []
            for task in tasks:
                results.append((yield task.join()))
            return results

        # Run program
        engine.run(program())

        # Check handler state
        unjoined = engine.spawn_handler.get_unjoined_tasks()
        assert len(unjoined) == 0

    def test_get_unjoined_tasks_excludes_fire_and_forget(self) -> None:
        """get_unjoined_tasks excludes fire_and_forget tasks."""
        engine = ProgramInterpreter()

        @do
        def worker(i: int) -> EffectGenerator[int]:
            return i

        @do
        def program() -> EffectGenerator[None]:
            # Spawn fire_and_forget tasks
            yield Spawn(worker(1), preferred_backend="thread", fire_and_forget=True)
            yield Spawn(worker(2), preferred_backend="thread", fire_and_forget=True)
            return None

        # Run program
        engine.run(program())

        # Check handler state - should be empty since all were fire_and_forget
        unjoined = engine.spawn_handler.get_unjoined_tasks()
        assert len(unjoined) == 0

    def test_clear_task_tracking_resets_state(self) -> None:
        """clear_task_tracking resets all tracking state."""
        engine = ProgramInterpreter()

        @do
        def worker() -> EffectGenerator[int]:
            return 42

        @do
        def program1() -> EffectGenerator[None]:
            yield Spawn(worker(), preferred_backend="thread")
            return None

        @do
        def program2() -> EffectGenerator[int]:
            task = yield Spawn(worker(), preferred_backend="thread")
            return (yield task.join())

        # Run first program (creates unjoined task)
        engine.run(program1())

        # Run second program (should have clean slate)
        result = engine.run(program2())

        # Second program should work correctly
        assert result.is_ok
        assert result.value == 42
