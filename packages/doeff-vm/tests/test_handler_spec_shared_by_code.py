"""Test that type resolution of effects for handlers whose closure is rebuilt per instance happens once per code object.

The VM resolves the effect-argument type annotations each time it installs a handler (doeff_vm._effect_types.handler_spec). The answer is remembered on the function object,
but a closure that a handler-returning function creates per instance is a different object every time, so it was resolved again each time (1,155 times in one
body-contract test of the custody store, with handler_spec taking about 5% of call CPU). The resolved types are also remembered on the function's code paired with the original annotation values they were resolved from, and a closure
of the same code uses that answer only when the annotation values are the same. If an annotation reads a closure variable (so its value differs per closure), each resolves its own annotation.
The key type (__doeff_passes__) returns a closure value, so it is not remembered by code and is read per function.
"""

import gc
from collections.abc import Callable
from dataclasses import dataclass

import pytest
from doeff_vm import _effect_types
from doeff_vm._effect_types import PassedEffects, declare_passes, handler_spec

from doeff import EffectBase, Pass, Resume, do, handler, run, with_handlers


@dataclass(frozen=True)
class Alpha(EffectBase):
    value: str


@dataclass(frozen=True)
class Beta(EffectBase):
    value: str


@handler
def fallback(effect: EffectBase, k):
    """Destination of effects no handler accepted (the outer catch-all)."""
    return (yield Resume(k, f"fallback:{type(effect).__name__}"))


def alpha_handler(tag: str) -> Callable[..., object]:
    """Shape of a handler rebuilt per instance - returns a closure of the same code every time (the annotations are module types, independent of the closure)."""

    @do
    def handle(effect: Alpha, k):
        return (yield Resume(k, f"{tag}:{effect.value}"))

    return handle


def kind_handler(kind: type, tag: str) -> Callable[..., object]:
    """A handler whose annotation reads the closure variable kind - even with the same code, the accepted type differs per closure."""

    @do
    def handle(effect: kind, k):
        return (yield Resume(k, f"{tag}:{effect.value}"))

    return handle


def other_alpha_handler(tag: str) -> Callable[..., object]:
    """A handler with the same annotation as alpha_handler but different code."""

    @do
    def handle(effect: Alpha, k):
        return (yield Resume(k, f"other-{tag}:{effect.value}"))

    return handle


@dataclass
class Resolutions:
    """Number of annotation resolutions (top-level calls of _types_of - increases by one each time one annotation is resolved)."""

    count: int = 0
    depth: int = 0


@pytest.fixture
def resolutions(monkeypatch: pytest.MonkeyPatch) -> Resolutions:
    """Count resolutions and start the per-code shared store empty (independent of test order)."""
    counted = Resolutions()
    resolve = _effect_types._types_of

    def counting(annotation: object, function: object) -> object:
        counted.count += counted.depth == 0
        counted.depth += 1
        try:
            return resolve(annotation, function)
        finally:
            counted.depth -= 1

    monkeypatch.setattr(_effect_types, "_types_of", counting)
    monkeypatch.setattr(_effect_types, "_SHARED_BY_CODE", {}, raising=False)
    return counted


@do
def alpha_then_beta():
    a = yield Alpha("1")
    b = yield Beta("2")
    return (a, b)


def test_a_a_closure_made_again_from_the_same_code_resolves_nothing(resolutions: Resolutions) -> None:
    first = alpha_handler("one")
    assert run(with_handlers([fallback, handler(first)], alpha_then_beta())) == ("one:1", "fallback:Beta")
    resolved_by_the_first = resolutions.count
    assert resolved_by_the_first >= 1, "the first closure's annotation is resolved"

    second = alpha_handler("two")
    assert second is not first and second.__wrapped__.__code__ is first.__wrapped__.__code__
    assert run(with_handlers([fallback, handler(second)], alpha_then_beta())) == ("two:1", "fallback:Beta")
    assert resolutions.count == resolved_by_the_first, "installing a closure of the same code with the same annotation a second time does not resolve again"
    assert handler_spec(second).effect_types == (Alpha,)


def test_b_closures_of_one_code_that_admit_different_types_do_not_mix(resolutions: Resolutions) -> None:
    alphas = kind_handler(Alpha, "alphas")
    betas = kind_handler(Beta, "betas")
    assert alphas.__wrapped__.__code__ is betas.__wrapped__.__code__

    assert run(with_handlers([fallback, handler(alphas)], alpha_then_beta())) == ("alphas:1", "fallback:Beta")
    # The second one of the same code accepts only Beta - borrowing Alpha's answer would let Beta pass through and fall to the fallback, and Alpha would come in
    assert run(with_handlers([fallback, handler(betas)], alpha_then_beta())) == ("fallback:Alpha", "betas:2")
    assert handler_spec(alphas).effect_types == (Alpha,)
    assert handler_spec(betas).effect_types == (Beta,)
    # Even after going back to the first, it does not borrow the second's answer
    assert run(with_handlers([fallback, handler(kind_handler(Alpha, "again"))], alpha_then_beta())) == ("again:1", "fallback:Beta")


def test_b_a_union_annotation_shares_only_when_its_members_are_the_same(resolutions: Resolutions) -> None:
    both = kind_handler(Alpha | Beta, "both")
    assert run(with_handlers([fallback, handler(both)], alpha_then_beta())) == ("both:1", "both:2")
    resolved = resolutions.count
    assert run(with_handlers([fallback, handler(kind_handler(Alpha | Beta, "again"))], alpha_then_beta())) == ("again:1", "again:2")
    assert resolutions.count == resolved, "even if each evaluation gives a new Alpha | Beta object, the same set of types is not resolved again"
    assert run(with_handlers([fallback, handler(kind_handler(Beta, "betas"))], alpha_then_beta())) == ("fallback:Alpha", "betas:2")


def test_b_the_passed_effects_of_closures_of_one_code_do_not_mix() -> None:
    def fence(passable: type) -> Callable[..., object]:
        """A handler of fence shape - the key type passable differs per closure."""

        @do
        def handle(effect: EffectBase, k):
            if not isinstance(effect, passable):
                return (yield Resume(k, f"fenced:{type(effect).__name__}"))
            return (yield Pass(effect, k))

        return declare_passes(handle, lambda: (passable, ()))

    alphas = fence(Alpha)
    betas = fence(Beta)
    assert handler_spec(alphas).passed == PassedEffects((Alpha,), ())
    assert handler_spec(betas).passed == PassedEffects((Beta,), ())
    assert run(with_handlers([fallback, handler(alphas)], alpha_then_beta())) == ("fallback:Alpha", "fenced:Beta")
    assert run(with_handlers([fallback, handler(betas)], alpha_then_beta())) == ("fenced:Alpha", "fallback:Beta")


def test_c_a_handler_of_another_code_is_resolved_on_its_own(resolutions: Resolutions) -> None:
    assert run(with_handlers([fallback, handler(alpha_handler("one"))], alpha_then_beta())) == ("one:1", "fallback:Beta")
    resolved = resolutions.count
    assert run(with_handlers([fallback, handler(other_alpha_handler("x"))], alpha_then_beta())) == ("other-x:1", "fallback:Beta")
    assert resolutions.count == resolved + 1, "different code is resolved separately even with the same annotation"


def test_d_the_shared_resolution_goes_with_its_code(resolutions: Resolutions) -> None:
    # A handler of a plain function (@do keeps the code alive in its own analysis cache, so the example of vanishing code is made with a plain function)
    namespace: dict[str, object] = {"Resume": Resume, "Alpha": Alpha}
    exec(
        compile("def make():\n    def handle(effect: Alpha, k):\n        return Resume(k, effect.value)\n    return handle\n",
                "<handler made at run time>", "exec"),
        namespace,
    )
    handler = namespace["make"]()
    assert handler_spec(handler).effect_types == (Alpha,)
    assert len(_effect_types._SHARED_BY_CODE) == 1, "one row per code"
    del handler, namespace
    gc.collect()
    assert _effect_types._SHARED_BY_CODE == {}, "when the code vanishes, the row vanishes too - code created during execution does not pile up without bound"
