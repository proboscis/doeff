from doeff_core_effects.cache import cache
from doeff_core_effects.handlers import await_handler, slog_handler
from doeff_core_effects.memo_handlers import in_memory_memo_handler, memo_terminal
from doeff_core_effects.scheduler import scheduled

from doeff import WithHandler, do, run


def _with_handlers(program, *handlers):
    wrapped = program
    for handler in reversed(handlers):
        wrapped = WithHandler(handler, wrapped)
    return wrapped


def test_cache_hit_with_terminal_keeps_existing_behavior():
    calls = {"count": 0}
    slog = slog_handler()

    @cache()
    @do
    def expensive(value):
        calls["count"] += 1
        return value * 2

    @do
    def program():
        first = yield expensive(21)
        second = yield expensive(21)
        return first, second

    wrapped = _with_handlers(
        program(),
        await_handler(),
        slog,
        memo_terminal,
        in_memory_memo_handler(),
    )

    assert run(scheduled(wrapped)) == (42, 42)
    assert calls["count"] == 1

    memo_messages = [entry["msg"] for entry in slog.log if entry["msg"].startswith("[memo-layer:")]
    assert sum("HIT" in msg for msg in memo_messages) == 1
    assert sum("MISS" in msg for msg in memo_messages) == 0
