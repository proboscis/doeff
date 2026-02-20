from __future__ import annotations

from doeff import EffectBase, Pass, Resume, WithHandler, default_handlers, do, run
from doeff.rust_vm import pass_


class _EffectA(EffectBase):
    pass


class _EffectB(EffectBase):
    pass


def test_pass_is_terminal_passthrough() -> None:
    resumed_after_pass = {"value": False}

    def inner_handler(effect, _k):
        if isinstance(effect, _EffectA):
            yield Pass()
            resumed_after_pass["value"] = True
            return "unreachable"
        yield Pass()

    def outer_handler(effect, k):
        if isinstance(effect, _EffectA):
            return (yield Resume(k, "handled-by-outer"))
        yield Pass()

    @do
    def body():
        value = yield _EffectA()
        return value

    result = run(
        WithHandler(outer_handler, WithHandler(inner_handler, body())),
        handlers=default_handlers(),
    )
    assert result.value == "handled-by-outer"
    assert resumed_after_pass["value"] is False


def test_pass_accepts_explicit_effect_argument() -> None:
    def remap_handler(effect, _k):
        if isinstance(effect, _EffectA):
            yield Pass(_EffectB())
            return "unreachable"
        yield Pass()

    def outer_handler(effect, k):
        if isinstance(effect, _EffectB):
            return (yield Resume(k, "handled-effect-b"))
        yield Pass()

    @do
    def body():
        value = yield _EffectA()
        return value

    result = run(
        WithHandler(outer_handler, WithHandler(remap_handler, body())),
        handlers=default_handlers(),
    )
    assert result.value == "handled-effect-b"


def test_pass_exports_are_available() -> None:
    assert Pass is not None
    assert pass_ is Pass
