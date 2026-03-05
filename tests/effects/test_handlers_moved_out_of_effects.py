from __future__ import annotations

import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_future_module_has_no_handler_definitions() -> None:
    source = _read("doeff/effects/future.py")
    assert "def sync_await_handler(" not in source
    assert "def async_await_handler(" not in source


def test_spawn_module_has_no_handler_definitions() -> None:
    source = _read("doeff/effects/spawn.py")
    assert "def spawn_intercept_handler(" not in source


def test_future_handlers_are_reexported_from_handlers_package() -> None:
    import doeff.effects.future as future_effects
    from doeff.handlers.await_handlers import (
        async_await_handler,
        python_async_syntax_escape_handler,
        sync_await_handler,
    )

    assert future_effects.sync_await_handler is sync_await_handler
    assert future_effects.async_await_handler is async_await_handler
    assert (
        future_effects.python_async_syntax_escape_handler
        is python_async_syntax_escape_handler
    )


def test_spawn_handler_is_reexported_from_handlers_package() -> None:
    spawn_effects = importlib.import_module("doeff.effects.spawn")
    from doeff.handlers.spawn_handler import spawn_intercept_handler

    assert spawn_effects.spawn_intercept_handler is spawn_intercept_handler
