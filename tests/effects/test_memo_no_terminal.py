from doeff_core_effects.cache import cache
from doeff_core_effects.handlers import await_handler, slog_handler
from doeff_core_effects.memo_handlers import in_memory_memo_handler
from doeff_core_effects.scheduler import scheduled

from doeff import do, run
from doeff import handler as _program_handler


def _with_handlers(program, *handlers):
    wrapped = program
    for handler in reversed(handlers):
        wrapped = _program_handler(handler)(wrapped)
    return wrapped


def test_cache_decorator_runs_with_memo_handler_but_no_terminal():
    calls = {"count": 0}

    @cache()
    @do
    def expensive(value):
        calls["count"] += 1
        return value * 2

    program = _with_handlers(
        expensive(21),
        await_handler(),
        slog_handler(),
        in_memory_memo_handler(),
    )

    assert run(scheduled(program)) == 42
    assert calls["count"] == 1
