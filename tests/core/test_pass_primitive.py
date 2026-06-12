from __future__ import annotations

import pytest

from doeff import Effect, EffectBase, Pass, Resume, do
from doeff import handler as _install_raw_handler
from tests._run_helpers import run_with_defaults


class _EffectA(EffectBase):
    pass


class _EffectB(EffectBase):
    pass


def test_pass_is_terminal_passthrough() -> None:
    resumed_after_pass = {"value": False}

    @do
    def inner_handler(effect: Effect, k):
        if isinstance(effect, _EffectA):
            yield Pass(effect, k)
            resumed_after_pass["value"] = True
            return "unreachable"
        yield Pass(effect, k)

    @do
    def outer_handler(effect: Effect, k):
        if isinstance(effect, _EffectA):
            return (yield Resume(k, "handled-by-outer"))
        yield Pass(effect, k)

    @do
    def body():
        value = yield _EffectA()
        return value

    result = run_with_defaults(
        _install_raw_handler(outer_handler)(_install_raw_handler(inner_handler)(body())),
    )
    assert result.value == "handled-by-outer"
    assert resumed_after_pass["value"] is False


def test_pass_rejects_explicit_effect_argument() -> None:
    with pytest.raises(TypeError):
        Pass(_EffectB())
