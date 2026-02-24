"""Tests for doeff-preset handlers."""

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doeff_preset import (
    DEFAULT_CONFIG,
    config_handlers,
    log_display_handlers,
    mock_handlers,
    preset_handlers,
    production_handlers,
)
from doeff_preset.effects import (
    PRESET_CONFIG_EFFECT,
    PRESET_CONFIG_KEY_PREFIX,
    PRESET_LOG_EFFECT,
    is_preset_config_key,
)
from doeff_preset.handlers import (
    mock_handlers as exported_mock_handlers,
)
from doeff_preset.handlers import (
    production_handlers as exported_production_handlers,
)

from doeff import (
    Ask,
    AskEffect,
    EffectBase,
    MissingEnvKeyError,
    Pass,
    Resume,
    WithHandler,
    WriterTellEffect,
    async_run,
    default_async_handlers,
    default_handlers,
    do,
    run,
    slog,
    tell,
)

HandlerFn = Callable[[Any, Any], Any]


def _run_with_handler(program, handler: HandlerFn, *, env=None, store=None):
    return run(
        WithHandler(handler, program),
        handlers=default_handlers(),
        env=env,
        store=store,
    )


async def _async_run_with_handler(
    program, handler: HandlerFn, *, env=None, store=None
):
    return await async_run(
        WithHandler(handler, program),
        handlers=default_async_handlers(),
        env=env,
        store=store,
    )


class TestCanonicalPackageLayout:
    """Tests for canonical effects/ + handlers/ package layout."""

    def test_effects_exports_core_effect_metadata(self):
        """effects package should expose metadata even without custom effect classes."""
        assert PRESET_CONFIG_EFFECT is AskEffect
        assert PRESET_LOG_EFFECT is WriterTellEffect
        assert PRESET_CONFIG_KEY_PREFIX == "preset."
        assert is_preset_config_key("preset.log_level")
        assert not is_preset_config_key("other.key")

    def test_handlers_exports_required_entrypoints(self):
        """handlers package should export production_handlers and mock_handlers."""
        assert callable(exported_production_handlers)
        assert callable(exported_mock_handlers)


class TestLogDisplayHandlers:
    """Tests for log_display_handlers."""

    def test_slog_is_displayed(self, capsys):
        """slog messages should be displayed by preset log handler."""

        @do
        def workflow():
            yield slog(step="start", msg="Hello")
            yield slog(step="end", msg="Goodbye")
            return "done"

        result = _run_with_handler(workflow(), log_display_handlers())

        assert result.value == "done"
        captured = capsys.readouterr()
        assert "Hello" in captured.err
        assert "Goodbye" in captured.err

    def test_tell_works_with_non_dict(self, capsys):
        """Regular tell messages should still work."""

        @do
        def workflow():
            yield tell("simple message")
            return "done"

        result = _run_with_handler(workflow(), log_display_handlers())

        assert result.value == "done"
        captured = capsys.readouterr()
        assert "simple message" not in captured.err


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

        result = _run_with_handler(workflow(), config_handlers())

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
        result = _run_with_handler(workflow(), config_handlers(defaults=custom_defaults))

        assert result.value == (False, "debug")

    def test_non_preset_ask_uses_env(self):
        """Non-preset.* Ask keys should use the environment."""

        @do
        def workflow():
            value = yield Ask("my_key")
            return value

        result = _run_with_handler(workflow(), config_handlers(), env={"my_key": "from_env"})

        assert result.value == "from_env"

    def test_missing_preset_key_raises(self):
        """Asking for unknown preset.* key should raise error."""

        @do
        def workflow():
            yield Ask("preset.unknown_key")
            return "done"

        result = _run_with_handler(workflow(), config_handlers())

        assert result.is_err()

    def test_missing_env_key_raises(self):
        """Asking for missing env key follows current reader behavior (error)."""

        @do
        def workflow():
            value = yield Ask("missing_key")
            return value

        result = _run_with_handler(workflow(), config_handlers())

        assert result.is_err()
        assert isinstance(result.error, MissingEnvKeyError)

    def test_with_handler_can_mock_preset_config(self):
        """Effect mocks should use WithHandler + Resume."""

        @do
        def workflow():
            show_logs = yield Ask("preset.show_logs")
            return show_logs

        def mock_preset_config(effect, k):
            if isinstance(effect, AskEffect) and effect.key == "preset.show_logs":
                return (yield Resume(k, False))
            yield Pass()

        program = WithHandler(mock_preset_config, workflow())
        result = _run_with_handler(program, preset_handlers())

        assert result.value is False


class TestPresetHandlers:
    """Tests for the combined preset_handlers()."""

    def test_preset_handlers_includes_log_display(self, capsys):
        """preset_handlers should include log display functionality."""

        @do
        def workflow():
            yield slog(msg="test message")
            return "done"

        result = _run_with_handler(workflow(), preset_handlers())

        assert result.value == "done"
        captured = capsys.readouterr()
        assert "test message" in captured.err

    def test_preset_handlers_includes_config(self):
        """preset_handlers should include config functionality."""

        @do
        def workflow():
            show_logs = yield Ask("preset.show_logs")
            return show_logs

        result = _run_with_handler(workflow(), preset_handlers())

        assert result.value is True

    def test_preset_handlers_with_custom_config(self):
        """preset_handlers should accept custom config defaults."""

        @do
        def workflow():
            log_level = yield Ask("preset.log_level")
            return log_level

        result = _run_with_handler(
            workflow(),
            preset_handlers(config_defaults={"preset.log_level": "warning"}),
        )

        assert result.value == "warning"

    def test_handlers_can_be_merged(self, capsys):
        """Preset handlers should merge with other handlers."""
        # Custom effect type for testing
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class CustomEffect(EffectBase):
            value: str

        def handle_custom(effect: CustomEffect, k):
            if isinstance(effect, CustomEffect):
                return (yield Resume(k, f"handled: {effect.value}"))
            yield Pass()

        @do
        def workflow():
            # Use both preset and custom effects
            yield slog(msg="using preset")
            custom_result = yield CustomEffect(value="test")
            return custom_result

        # Stack handlers explicitly: inner custom handler shadows outer preset handler.
        stacked_program = WithHandler(
            preset_handlers(),
            WithHandler(handle_custom, workflow()),
        )
        result = run(stacked_program, handlers=default_handlers())

        assert result.value == "handled: test"
        captured = capsys.readouterr()
        assert "using preset" in captured.err


class TestProductionAndMockHandlers:
    """Tests for explicit production and testing handler entrypoints."""

    def test_production_handlers_include_display_and_config(self, capsys):
        """production_handlers should display slog and resolve preset config."""

        @do
        def workflow():
            yield slog(msg="from production")
            level = yield Ask("preset.log_level")
            return level

        result = _run_with_handler(workflow(), production_handlers())

        assert result.value == "info"
        captured = capsys.readouterr()
        assert "from production" in captured.err

    def test_mock_handlers_disable_display(self, capsys):
        """mock_handlers should not print structured logs to stderr."""

        @do
        def workflow():
            yield slog(msg="from mock")
            return "done"

        result = _run_with_handler(workflow(), mock_handlers())

        assert result.value == "done"
        captured = capsys.readouterr()
        assert "from mock" not in captured.err

    def test_mock_handlers_support_custom_config_defaults(self):
        """mock_handlers should still provide deterministic config values."""

        @do
        def workflow():
            return (yield Ask("preset.show_logs"))

        result = _run_with_handler(
            workflow(),
            mock_handlers(config_defaults={"preset.show_logs": False}),
        )

        assert result.value is False


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
    """Tests verifying handlers work with sync/async entrypoints."""

    def test_sync_runtime(self):
        """Handlers should work with sync run()."""

        @do
        def workflow():
            yield slog(msg="sync test")
            config = yield Ask("preset.show_logs")
            return config

        result = _run_with_handler(workflow(), preset_handlers())

        assert result.is_ok()
        assert result.value is True

    @pytest.mark.asyncio
    async def test_async_runtime(self):
        """Handlers should work with async_run()."""

        @do
        def workflow():
            yield slog(msg="async test")
            config = yield Ask("preset.show_logs")
            return config

        result = await _async_run_with_handler(workflow(), preset_handlers())

        assert result.is_ok()
        assert result.value is True
