from __future__ import annotations

import inspect

from doeff_core_effects.cache import cache
from doeff_core_effects.handlers import await_handler, slog_handler
from doeff_core_effects.memo_handlers import memo_handler, memo_terminal
from doeff_core_effects.scheduler import scheduled
from doeff_core_effects.storage import InMemoryStorage

from doeff import WithHandler, do, run


def _run_with_handlers(program, *handlers):
    wrapped = program
    for handler in reversed(handlers):
        wrapped = WithHandler(handler, wrapped)
    return run(scheduled(wrapped))


def test_cache_with_terminal_still_hits_l1_and_writes_through():
    calls = {"count": 0}
    l1 = InMemoryStorage()
    l2 = InMemoryStorage()

    @cache()
    @do
    def cached_double(value: int):
        calls["count"] += 1
        return value * 2

    @do
    def program():
        first = yield cached_double(21)
        second = yield cached_double(21)
        return (first, second)

    result = _run_with_handlers(
        program(),
        await_handler(),
        slog_handler(),
        memo_terminal,
        memo_handler(l2, name="L2"),
        memo_handler(l1, name="L1"),
    )

    assert result == (42, 42)
    assert calls["count"] == 1
    assert len(l1) == 1
    assert len(l2) == 1
    assert list(l1.items()) == list(l2.items())


def test_memo_terminal_docstring_marks_it_deprecated():
    doc = inspect.getdoc(memo_terminal)
    assert doc is not None
    assert "deprecated" in doc.lower()
