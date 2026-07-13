from dataclasses import dataclass

from doeff_core_effects.handlers import await_handler, slog_handler, state
from doeff_core_effects.memo_handlers import in_memory_memo_handler, make_memo_rewriter
from doeff_core_effects.scheduler import scheduled

from doeff import EffectBase, Pass, Resume, do, run
from doeff import handler as _install_raw_handler


@dataclass(frozen=True)
class Lookup(EffectBase):
    key: str


def _with_handlers(program, *handlers):
    wrapped = program
    for handler in reversed(handlers):
        wrapped = _install_raw_handler(handler)(wrapped)
    return wrapped


def _lookup_handler(calls):
    @do
    def handler(effect, k):
        if not isinstance(effect, Lookup):
            yield Pass(effect, k)
            return

        calls["count"] += 1
        return (yield Resume(k, f"value:{effect.key}"))

    return _install_raw_handler(handler)


def test_memo_rewriter_uses_storage_without_terminal_and_hits_on_second_call():
    calls = {"count": 0}
    rewriter = make_memo_rewriter(Lookup, key_fn=lambda effect: effect.key)

    @do
    def program():
        first = yield Lookup("alpha")
        second = yield Lookup("alpha")
        return first, second

    wrapped = _with_handlers(
        program(),
        await_handler(),
        state(),
        slog_handler,
        _lookup_handler(calls),
        in_memory_memo_handler(),
        rewriter,
    )

    assert run(scheduled(wrapped)) == ("value:alpha", "value:alpha")
    assert calls["count"] == 1


def test_memo_rewriter_falls_through_without_memo_storage_and_does_not_cache():
    calls = {"count": 0}
    rewriter = make_memo_rewriter(Lookup, key_fn=lambda effect: effect.key)

    @do
    def program():
        first = yield Lookup("alpha")
        second = yield Lookup("alpha")
        return first, second

    wrapped = _with_handlers(
        program(),
        state(),
        slog_handler,
        _lookup_handler(calls),
        rewriter,
    )

    assert run(wrapped) == ("value:alpha", "value:alpha")
    assert calls["count"] == 2
