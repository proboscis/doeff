"""Tests for doeff-preset handlers."""

import pytest

from doeff import Ask, SyncRuntime, do
from doeff.effects.writer import slog, tell
from doeff_preset import (
    DEFAULT_CONFIG,
    config_handlers,
    log_display_handlers,
    preset_handlers,
)


class TestLogDisplayHandlers:
    """Tests for log_display_handlers."""

    def test_slog_accumulates_in_log(self):
        """slog messages should accumulate in the writer log."""
        @do
        def workflow():
            yield slog(step="start", msg="Hello")
            yield slog(step="end", msg="Goodbye")
            return "done"

        runtime = SyncRuntime(handlers=log_display_handlers())
        result = runtime.run(workflow())

        assert result.value == "done"
        assert len(result.log) == 2
        assert result.log[0] == {"step": "start", "msg": "Hello"}
        assert result.log[1] == {"step": "end", "msg": "Goodbye"}

    def test_tell_works_with_non_dict(self):
        """Regular tell messages should still work."""
        @do
        def workflow():
            yield tell("simple message")
            return "done"

        runtime = SyncRuntime(handlers=log_display_handlers())
        result = runtime.run(workflow())

        assert result.value == "done"
        assert len(result.log) == 1
        assert result.log[0] == "simple message"


class TestConfigHandlers:
    """Tests for config_handlers."""

    def test_default_config_values(self):
        """Ask for preset.* keys should return default values."""
        @do
        def workflow():
            show_logs = yield Ask("preset.show_logs")
            log_level = yield Ask("preset.log_level")
            log_format = yield Ask("preset.log_format")
            return (show_logs, log_level, log_format)

        runtime = SyncRuntime(handlers=config_handlers())
        result = runtime.run(workflow())

        assert result.value == (True, "info", "rich")

    def test_custom_config_overrides(self):
        """Custom defaults should override the built-in defaults."""
        @do
        def workflow():
            show_logs = yield Ask("preset.show_logs")
            log_level = yield Ask("preset.log_level")
            return (show_logs, log_level)

        custom_defaults = {
            "preset.show_logs": False,
            "preset.log_level": "debug",
        }
        runtime = SyncRuntime(handlers=config_handlers(defaults=custom_defaults))
        result = runtime.run(workflow())

        assert result.value == (False, "debug")

    def test_non_preset_ask_uses_env(self):
        """Non-preset.* Ask keys should use the environment."""
        @do
        def workflow():
            value = yield Ask("my_key")
            return value

        runtime = SyncRuntime(handlers=config_handlers())
        result = runtime.run(workflow(), env={"my_key": "from_env"})

        assert result.value == "from_env"

    def test_missing_preset_key_raises(self):
        """Asking for unknown preset.* key should raise error."""
        @do
        def workflow():
            yield Ask("preset.unknown_key")
            return "done"

        runtime = SyncRuntime(handlers=config_handlers())
        result = runtime.run(workflow())

        assert result.is_err()

    def test_missing_env_key_raises(self):
        """Asking for missing env key should raise error."""
        @do
        def workflow():
            yield Ask("missing_key")
            return "done"

        runtime = SyncRuntime(handlers=config_handlers())
        result = runtime.run(workflow())

        assert result.is_err()


class TestPresetHandlers:
    """Tests for the combined preset_handlers()."""

    def test_preset_handlers_includes_log_display(self):
        """preset_handlers should include log display functionality."""
        @do
        def workflow():
            yield slog(msg="test message")
            return "done"

        runtime = SyncRuntime(handlers=preset_handlers())
        result = runtime.run(workflow())

        assert result.value == "done"
        assert len(result.log) == 1
        assert result.log[0] == {"msg": "test message"}

    def test_preset_handlers_includes_config(self):
        """preset_handlers should include config functionality."""
        @do
        def workflow():
            show_logs = yield Ask("preset.show_logs")
            return show_logs

        runtime = SyncRuntime(handlers=preset_handlers())
        result = runtime.run(workflow())

        assert result.value is True

    def test_preset_handlers_with_custom_config(self):
        """preset_handlers should accept custom config defaults."""
        @do
        def workflow():
            log_level = yield Ask("preset.log_level")
            return log_level

        runtime = SyncRuntime(handlers=preset_handlers(
            config_defaults={"preset.log_level": "warning"}
        ))
        result = runtime.run(workflow())

        assert result.value == "warning"

    def test_handlers_can_be_merged(self):
        """Preset handlers should merge with other handlers."""
        from doeff.cesk.frames import ContinueValue

        # Custom effect type for testing
        from dataclasses import dataclass
        from doeff.effects.base import EffectBase

        @dataclass(frozen=True)
        class CustomEffect(EffectBase):
            value: str

        def handle_custom(effect, task_state, store):
            return ContinueValue(
                value=f"handled: {effect.value}",
                env=task_state.env,
                store=store,
                k=task_state.kontinuation,
            )

        @do
        def workflow():
            # Use both preset and custom effects
            yield slog(msg="using preset")
            custom_result = yield CustomEffect(value="test")
            return custom_result

        # Merge handlers: domain handlers win
        handlers = {**preset_handlers(), CustomEffect: handle_custom}
        runtime = SyncRuntime(handlers=handlers)
        result = runtime.run(workflow())

        assert result.value == "handled: test"
        assert len(result.log) == 1


class TestDefaultConfig:
    """Tests for DEFAULT_CONFIG export."""

    def test_default_config_has_expected_keys(self):
        """DEFAULT_CONFIG should have the documented keys."""
        assert "preset.show_logs" in DEFAULT_CONFIG
        assert "preset.log_level" in DEFAULT_CONFIG
        assert "preset.log_format" in DEFAULT_CONFIG

    def test_default_config_values(self):
        """DEFAULT_CONFIG should have the documented default values."""
        assert DEFAULT_CONFIG["preset.show_logs"] is True
        assert DEFAULT_CONFIG["preset.log_level"] == "info"
        assert DEFAULT_CONFIG["preset.log_format"] == "rich"


class TestSyncAndAsyncCompatibility:
    """Tests verifying handlers work with both runtime types."""

    def test_sync_runtime(self):
        """Handlers should work with SyncRuntime."""
        @do
        def workflow():
            yield slog(msg="sync test")
            config = yield Ask("preset.show_logs")
            return config

        runtime = SyncRuntime(handlers=preset_handlers())
        result = runtime.run(workflow())

        assert result.is_ok()
        assert result.value is True

    @pytest.mark.asyncio
    async def test_async_runtime(self):
        """Handlers should work with AsyncRuntime."""
        from doeff import AsyncRuntime

        @do
        def workflow():
            yield slog(msg="async test")
            config = yield Ask("preset.show_logs")
            return config

        runtime = AsyncRuntime(handlers=preset_handlers())
        result = await runtime.run(workflow())

        assert result.is_ok()
        assert result.value is True
