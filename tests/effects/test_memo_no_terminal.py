from __future__ import annotations

from doeff_core_effects.cache import cache
from doeff_core_effects.handlers import await_handler, slog_handler
from doeff_core_effects.memo_handlers import memo_handler
from doeff_core_effects.scheduler import scheduled
from doeff_core_effects.storage import InMemoryStorage

from doeff import WithHandler, do, run


def _run_with_handlers(program, *handlers):
    wrapped = program
    for handler in reversed(handlers):
        wrapped = WithHandler(handler, wrapped)
    return run(scheduled(wrapped))


def test_cache_without_memo_terminal_falls_through_to_compute():
    calls = {"count": 0}
    storage = InMemoryStorage()

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
        memo_handler(storage, name="memory"),
    )

    assert result == (42, 42)
    assert calls["count"] == 1
    assert len(storage) == 1
