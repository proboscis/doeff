from dataclasses import dataclass

import pytest
from doeff_core_effects.handlers import slog_handler
from doeff_core_effects.memo_handlers import make_memo_rewriter

from doeff import EffectBase, UnhandledEffect, WithHandler, do, run


@dataclass(frozen=True)
class Lookup(EffectBase):
    key: str


def _with_handlers(program, *handlers):
    wrapped = program
    for handler in reversed(handlers):
        wrapped = WithHandler(handler, wrapped)
    return wrapped


def test_memo_rewriter_does_not_swallow_compute_path_unhandled_effect():
    rewriter = make_memo_rewriter(Lookup, key_fn=lambda effect: effect.key)

    @do
    def program():
        return (yield Lookup("alpha"))

    wrapped = _with_handlers(program(), slog_handler(), rewriter)

    with pytest.raises(UnhandledEffect):
        run(wrapped)
