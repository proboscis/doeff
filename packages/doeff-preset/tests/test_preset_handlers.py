"""Tests for doeff-preset handlers."""

import inspect
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doeff import Ask, Delegate, WithHandler, async_run, default_handlers, do, run
from doeff.effects.writer import slog, tell
from doeff_preset import (
    DEFAULT_CONFIG,
    config_handlers,
    log_display_handlers,
    preset_handlers,
)


HandlerFn = Callable[[Any, Any], Any]


def _wrap_with_handler_map(program, handler_map: dict[type, HandlerFn]):
    wrapped = program
    for effect_type, handler in reversed(list(handler_map.items())):

        def typed_handler(effect, k, _effect_type=effect_type, _handler=handler):
            if isinstance(effect, _effect_type):
                result = _handler(effect, k)
                if inspect.isgenerator(result):
                    return (yield from result)
                return result
            yield Delegate()

        wrapped = WithHandler(handler=typed_handler, expr=wrapped)
    return wrapped


def _run_with_handler_map(program, handler_map: dict[type, HandlerFn], *, env=None, store=None):
    wrapped = _wrap_with_handler_map(program, handler_map)
    return run(wrapped, handlers=default_handlers(), env=env, store=store)


async def _async_run_with_handler_map(
    program, handler_map: dict[type, HandlerFn], *, env=None, store=None
):
    wrapped = _wrap_with_handler_map(program, handler_map)
    return await async_run(wrapped, handlers=default_handlers(), env=env, store=store)


class TestLogDisplayHandlers:
    """Tests for log_display_handlers."""

    def test_slog_is_displayed(self, capsys):
        """slog messages should be displayed by preset log handler."""

        @do
        def workflow():
            yield slog(step="start", msg="Hello")
            yield slog(step="end", msg="Goodbye")
            return "done"

        result = _run_with_handler_map(workflow(), log_display_handlers())

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

        result = _run_with_handler_map(workflow(), log_display_handlers())

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

        result = _run_with_handler_map(workflow(), config_handlers())

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
        result = _run_with_handler_map(workflow(), config_handlers(defaults=custom_defaults))

        assert result.value == (False, "debug")

    def test_non_preset_ask_uses_env(self):
        """Non-preset.* Ask keys should use the environment."""

        @do
        def workflow():
            value = yield Ask("my_key")
            return value

        result = _run_with_handler_map(workflow(), config_handlers(), env={"my_key": "from_env"})

        assert result.value == "from_env"

    def test_missing_preset_key_raises(self):
        """Asking for unknown preset.* key should raise error."""

        @do
        def workflow():
            yield Ask("preset.unknown_key")
            return "done"

        result = _run_with_handler_map(workflow(), config_handlers())

        assert result.is_err()

    def test_missing_env_key_returns_none(self):
        """Asking for missing env key follows current reader behavior (None)."""

        @do
        def workflow():
            value = yield Ask("missing_key")
            return value

        result = _run_with_handler_map(workflow(), config_handlers())

        assert result.is_ok()
        assert result.value is None


class TestPresetHandlers:
    """Tests for the combined preset_handlers()."""

    def test_preset_handlers_includes_log_display(self, capsys):
        """preset_handlers should include log display functionality."""

        @do
        def workflow():
            yield slog(msg="test message")
            return "done"

        result = _run_with_handler_map(workflow(), preset_handlers())

        assert result.value == "done"
        captured = capsys.readouterr()
        assert "test message" in captured.err

    def test_preset_handlers_includes_config(self):
        """preset_handlers should include config functionality."""

        @do
        def workflow():
            show_logs = yield Ask("preset.show_logs")
            return show_logs

        result = _run_with_handler_map(workflow(), preset_handlers())

        assert result.value is True

    def test_preset_handlers_with_custom_config(self):
        """preset_handlers should accept custom config defaults."""

        @do
        def workflow():
            log_level = yield Ask("preset.log_level")
            return log_level

        result = _run_with_handler_map(
            workflow(),
            preset_handlers(config_defaults={"preset.log_level": "warning"}),
        )

        assert result.value == "warning"

    def test_handlers_can_be_merged(self, capsys):
        """Preset handlers should merge with other handlers."""
        from doeff import Delegate, Resume

        # Custom effect type for testing
        from dataclasses import dataclass
        from doeff.effects.base import EffectBase

        @dataclass(frozen=True)
        class CustomEffect(EffectBase):
            value: str

        def handle_custom(effect: CustomEffect, k):
            if isinstance(effect, CustomEffect):
                return (yield Resume(k, f"handled: {effect.value}"))
            yield Delegate()

        @do
        def workflow():
            # Use both preset and custom effects
            yield slog(msg="using preset")
            custom_result = yield CustomEffect(value="test")
            return custom_result

        # Merge handlers: domain handlers win
        handlers = {**preset_handlers(), CustomEffect: handle_custom}
        result = _run_with_handler_map(workflow(), handlers)

        assert result.value == "handled: test"
        captured = capsys.readouterr()
        assert "using preset" in captured.err


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

        result = _run_with_handler_map(workflow(), preset_handlers())

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

        result = await _async_run_with_handler_map(workflow(), preset_handlers())

        assert result.is_ok()
        assert result.value is True
