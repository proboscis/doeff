from __future__ import annotations

from doeff_core_effects.handlers import await_handler, slog_handler
from doeff_core_effects.memo_handlers import make_memo_rewriter, memo_handler
from doeff_core_effects.scheduler import scheduled
from doeff_core_effects.storage import InMemoryStorage

from doeff import EffectBase, Pass, Resume, WithHandler, do, run


class FetchValue(EffectBase):
    def __init__(self, key: str) -> None:
        super().__init__()
        self.key = key


def _make_fetch_value_handler():
    calls = {"count": 0}

    @do
    def fetch_value_handler(effect, k):
        if not isinstance(effect, FetchValue):
            yield Pass(effect, k)
            return
        calls["count"] += 1
        value = f"value:{effect.key}"
        return (yield Resume(k, value))

    return fetch_value_handler, calls


def _run_with_handlers(program, *handlers):
    wrapped = program
    for handler in reversed(handlers):
        wrapped = WithHandler(handler, wrapped)
    return run(scheduled(wrapped))


def test_memo_rewriter_without_terminal_uses_original_handler():
    storage = InMemoryStorage()
    fetch_value_handler, calls = _make_fetch_value_handler()

    @do
    def program():
        first = yield FetchValue("alpha")
        second = yield FetchValue("alpha")
        return (first, second)

    result = _run_with_handlers(
        program(),
        await_handler(),
        slog_handler(),
        memo_handler(storage, name="memory"),
        fetch_value_handler,
        make_memo_rewriter(FetchValue, key_fn=lambda effect: effect.key),
    )

    assert result == ("value:alpha", "value:alpha")
    assert calls["count"] == 1
    assert len(storage) == 1
