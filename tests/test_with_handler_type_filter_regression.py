"""Regression: typed WithHandler type-filter skip is not equivalent to Pass().

All tests use the same nesting:

    catch_all(outer) → alpha_handler(inner) → program

alpha_handler only handles Alpha effects. catch_all handles everything.

Programs yield different sequences of Alpha/Beta to isolate when the
type-filtered inner handler stops being dispatched.
"""

from dataclasses import dataclass

import doeff_vm
import pytest

from doeff import Effect, EffectBase, EffectGenerator, default_handlers, do, run
from doeff_vm import WithHandler


@dataclass(frozen=True)
class Alpha(EffectBase):
    value: str


@dataclass(frozen=True)
class Beta(EffectBase):
    value: str


@do
def alpha_handler_typed(effect: Alpha, k):
    return (yield doeff_vm.Resume(k, f"alpha:{effect.value}"))


@do
def alpha_handler_isinstance(effect: Effect, k):
    if not isinstance(effect, Alpha):
        yield doeff_vm.Pass()
        return
    return (yield doeff_vm.Resume(k, f"alpha:{effect.value}"))


@do
def catch_all(effect: Effect, k):
    if isinstance(effect, Alpha):
        return (yield doeff_vm.Resume(k, f"fallback_alpha:{effect.value}"))
    if isinstance(effect, Beta):
        return (yield doeff_vm.Resume(k, f"fallback_beta:{effect.value}"))
    yield doeff_vm.Pass()


def _wrap(inner_handler, program):
    return WithHandler(catch_all, WithHandler(inner_handler, program))


# -- programs ----------------------------------------------------------------


@do
def prog_alpha() -> EffectGenerator[str]:
    return (yield Alpha(value="1"))


@do
def prog_alpha_alpha() -> EffectGenerator[tuple[str, str]]:
    a1 = yield Alpha(value="1")
    a2 = yield Alpha(value="2")
    return (a1, a2)


@do
def prog_beta_beta() -> EffectGenerator[tuple[str, str]]:
    b1 = yield Beta(value="1")
    b2 = yield Beta(value="2")
    return (b1, b2)


@do
def prog_beta_alpha() -> EffectGenerator[tuple[str, str]]:
    b = yield Beta(value="1")
    a = yield Alpha(value="2")
    return (b, a)


@do
def prog_alpha_beta() -> EffectGenerator[tuple[str, str]]:
    a = yield Alpha(value="1")
    b = yield Beta(value="2")
    return (a, b)


@do
def prog_alpha_beta_alpha() -> EffectGenerator[tuple[str, str, str]]:
    a1 = yield Alpha(value="1")
    b = yield Beta(value="2")
    a2 = yield Alpha(value="3")
    return (a1, b, a2)


# -- isinstance+Pass (baseline — all should pass) ---------------------------


class TestIsinstancePass:
    def test_alpha(self):
        result = run(_wrap(alpha_handler_isinstance, prog_alpha()))
        assert result.is_ok(), result.error
        assert result.value == "alpha:1"

    def test_alpha_alpha(self):
        result = run(_wrap(alpha_handler_isinstance, prog_alpha_alpha()))
        assert result.is_ok(), result.error
        assert result.value == ("alpha:1", "alpha:2")

    def test_beta_beta(self):
        result = run(_wrap(alpha_handler_isinstance, prog_beta_beta()))
        assert result.is_ok(), result.error
        assert result.value == ("fallback_beta:1", "fallback_beta:2")

    def test_beta_alpha(self):
        result = run(_wrap(alpha_handler_isinstance, prog_beta_alpha()))
        assert result.is_ok(), result.error
        assert result.value == ("fallback_beta:1", "alpha:2")

    def test_alpha_beta(self):
        result = run(_wrap(alpha_handler_isinstance, prog_alpha_beta()))
        assert result.is_ok(), result.error
        assert result.value == ("alpha:1", "fallback_beta:2")

    def test_alpha_beta_alpha(self):
        result = run(_wrap(alpha_handler_isinstance, prog_alpha_beta_alpha()))
        assert result.is_ok(), result.error
        assert result.value == ("alpha:1", "fallback_beta:2", "alpha:3")


# -- typed handler (should produce identical results) ------------------------


class TestTypedHandler:
    def test_alpha(self):
        result = run(_wrap(alpha_handler_typed, prog_alpha()))
        assert result.is_ok(), result.error
        assert result.value == "alpha:1"

    def test_alpha_alpha(self):
        result = run(_wrap(alpha_handler_typed, prog_alpha_alpha()))
        assert result.is_ok(), result.error
        assert result.value == ("alpha:1", "alpha:2")

    def test_beta_beta(self):
        result = run(_wrap(alpha_handler_typed, prog_beta_beta()))
        assert result.is_ok(), result.error
        assert result.value == ("fallback_beta:1", "fallback_beta:2")

    def test_beta_alpha(self):
        result = run(_wrap(alpha_handler_typed, prog_beta_alpha()))
        assert result.is_ok(), result.error
        assert result.value == ("fallback_beta:1", "alpha:2")

    def test_alpha_beta(self):
        result = run(_wrap(alpha_handler_typed, prog_alpha_beta()))
        assert result.is_ok(), result.error
        assert result.value == ("alpha:1", "fallback_beta:2")

    def test_alpha_beta_alpha(self):
        result = run(_wrap(alpha_handler_typed, prog_alpha_beta_alpha()))
        assert result.is_ok(), result.error
        assert result.value == ("alpha:1", "fallback_beta:2", "alpha:3")
