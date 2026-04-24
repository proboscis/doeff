"""Test-only helpers for composing default handlers and running programs.

Lives in tests/ so we do NOT re-introduce removed public API in doeff itself.
The old `run(program, handlers=default_handlers(), env=..., store=..., ...)`
surface was removed in the rebuild; tests that want the "everything wired up"
behaviour use `run_with_defaults(...)` here instead.

Individual tests that only need a subset of handlers should compose them
inline with WithHandler rather than reach for this helper.
"""

from __future__ import annotations

from typing import Any

from doeff import Err, Ok, WithHandler, run as _run
from doeff_core_effects.handlers import (
    await_handler,
    lazy_ask,
    listen_handler,
    local_handler,
    reader,
    slog_handler,
    state,
    try_handler,
    writer,
)
from doeff_core_effects.scheduler import scheduled


def default_handlers(env: Any = None, store: Any = None) -> list[Any]:
    """Return the standard handler chain used in legacy tests.

    Outer → inner order: reader, state, writer, try, slog, local, listen,
    await, lazy_ask. Tests that only exercise a subset should compose
    handlers explicitly.
    """
    initial = store if store is not None else {}
    return [
        reader(env=env),
        state(initial=initial),
        writer(),
        try_handler,
        slog_handler(),
        local_handler,
        listen_handler,
        await_handler(),
        lazy_ask(env=env),
    ]


def wrap_with_defaults(program: Any, env: Any = None, store: Any = None) -> Any:
    """Wrap ``program`` with the default handler chain + scheduler."""
    wrapped = program
    for handler in reversed(default_handlers(env=env, store=store)):
        wrapped = WithHandler(handler, wrapped)
    return scheduled(wrapped)


def run_with_defaults(
    program: Any,
    env: Any = None,
    store: Any = None,
    **_legacy: Any,
) -> Any:
    """Run a program with the default handler chain.

    Returns ``Ok(value)`` on success and ``Err(exception)`` on failure so
    legacy tests that expected ``run(..., handlers=default_handlers())`` to
    return a Result can keep calling ``result.is_ok()`` / ``result.value``
    without rewriting every call site.

    ``_legacy`` absorbs removed kwargs (``trace``, ``print_doeff_trace``) from
    pre-rebuild tests so call sites don't need a second migration step.
    """
    try:
        value = _run(wrap_with_defaults(program, env=env, store=store))
    except BaseException as exc:
        return Err(exc)
    return Ok(value)
