"""Test-only helpers for composing default handlers and running programs.

Lives in tests/ so we do NOT re-introduce removed public API in doeff itself.
The old `run(program, handlers=default_handlers(), env=..., store=..., ...)`
surface was removed in the rebuild; tests that want the "everything wired up"
behaviour use `run_with_defaults(...)` here instead.

Individual tests that only need a subset of handlers should compose them
inline with WithHandler rather than reach for this helper.

NOT private despite the leading underscore: test files under seven different
`packages/*/tests/` trees import `run_with_defaults` from here. The name is
kept for churn reasons only -- treat this module as a shared entry point and
migrate every importer together if it moves.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

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

from doeff import Err, Ok
from doeff import handler as _program_handler
from doeff import run as _run


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
        writer,
        try_handler,
        slog_handler,
        local_handler,
        listen_handler,
        await_handler(),
        lazy_ask(env=env),
    ]


def wrap_with_defaults(
    program: Any,
    env: Any = None,
    store: Any = None,
    outer_handlers: Sequence[Any] = (),
) -> Any:
    """Wrap ``program`` with the default handler chain + scheduler.

    ``outer_handlers`` reproduces the pre-rebuild
    ``run(p, handlers=[extra, *default_handlers()])`` shape: entries are
    installed *outside* the default chain, in the same order the legacy list
    had them (first entry outermost).
    """
    wrapped = program
    for handler in reversed([*outer_handlers, *default_handlers(env=env, store=store)]):
        wrapped = _program_handler(handler)(wrapped)
    return scheduled(wrapped)


#: Keyword arguments the pre-rebuild ``run()`` accepted and today's runtime
#: ignores. Absorbed silently; anything *not* on this list is a typo and must
#: raise rather than be swallowed (a misspelled ``env=`` would otherwise run
#: the program with no environment and look green).
_ABSORBED_LEGACY_KWARGS = frozenset({"trace", "print_doeff_trace"})


def run_with_defaults(
    program: Any,
    env: Any = None,
    store: Any = None,
    outer_handlers: Sequence[Any] = (),
    **legacy: Any,
) -> Any:
    """Run a program with the default handler chain.

    Returns ``Ok(value)`` on success and ``Err(exception)`` on failure so
    legacy tests that expected ``run(..., handlers=default_handlers())`` to
    return a Result can keep calling ``result.is_ok()`` / ``result.value``
    without rewriting every call site.

    Only the kwargs in :data:`_ABSORBED_LEGACY_KWARGS` are ignored; any other
    keyword raises ``TypeError``.

    Only ``Exception`` is converted to ``Err``. ``BaseException`` must pass
    through: ``pytest.skip()`` / ``pytest.fail()`` raise
    ``_pytest.outcomes.OutcomeException`` (a ``BaseException``), and catching
    those here would turn a skipped test into an ``Err`` result -- and would
    swallow ``KeyboardInterrupt`` during long runs.
    """
    unknown = set(legacy) - _ABSORBED_LEGACY_KWARGS
    if unknown:
        raise TypeError(
            f"run_with_defaults() got unexpected keyword argument(s): {', '.join(sorted(unknown))}"
        )
    try:
        value = _run(
            wrap_with_defaults(program, env=env, store=store, outer_handlers=outer_handlers)
        )
    except Exception as exc:
        return Err(exc)
    return Ok(value)
