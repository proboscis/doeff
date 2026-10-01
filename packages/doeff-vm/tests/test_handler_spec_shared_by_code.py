"""例ごとに作り直す閉包の handler の、effect の型の解きを code ごとに 1 回にする事の検(agora-redesign #2422)。

VM は handler を据える度に effect の引数の型の註を解く(doeff_vm._effect_types.handler_spec)。答えは関数のオブジェクトに
覚えるが、handler を返す関数が例ごとに作る閉包は毎回別のオブジェクトなので、毎回解き直していた(預かり所の本体の契約の検の
1 本で 1,155 回・handler_spec が call CPU の約 5%)。解いた型は関数の code にも、解いた元の註の値と組で覚え、同じ code の
閉包は註の値が同じ時だけその答えを使う。註が閉包の変数を読む(閉包ごとに値が違う)なら、それぞれ自分の註を解く。
番の型(__doeff_passes__)は閉包の値を返すので code では覚えず、関数ごとに読む。
"""

import gc
from collections.abc import Callable
from dataclasses import dataclass

import pytest
from doeff_vm import _effect_types
from doeff_vm._effect_types import PassedEffects, declare_passes, handler_spec

from doeff import EffectBase, Pass, Resume, do, run, with_handlers


@dataclass(frozen=True)
class Alpha(EffectBase):
    value: str


@dataclass(frozen=True)
class Beta(EffectBase):
    value: str


@do
def fallback(effect: EffectBase, k):
    """どの handler も受けなかった effect の行き先(外側の総受け)。"""
    return (yield Resume(k, f"fallback:{type(effect).__name__}"))


def alpha_handler(tag: str) -> Callable[..., object]:
    """例ごとに作り直す handler の形 — 同じ code の閉包を毎回返す(註は module の型で、閉包に依らない)。"""

    @do
    def handle(effect: Alpha, k):
        return (yield Resume(k, f"{tag}:{effect.value}"))

    return handle


def kind_handler(kind: type, tag: str) -> Callable[..., object]:
    """註が閉包の変数 kind を読む handler — 同じ code でも閉包ごとに受ける型が違う。"""

    @do
    def handle(effect: kind, k):
        return (yield Resume(k, f"{tag}:{effect.value}"))

    return handle


def other_alpha_handler(tag: str) -> Callable[..., object]:
    """alpha_handler と同じ註で別の code の handler。"""

    @do
    def handle(effect: Alpha, k):
        return (yield Resume(k, f"other-{tag}:{effect.value}"))

    return handle


@dataclass
class Resolutions:
    """註の型の解きの回数(_types_of の最上段の呼び — 註 1 つを解く度に 1 つ増える)。"""

    count: int = 0
    depth: int = 0


@pytest.fixture
def resolutions(monkeypatch: pytest.MonkeyPatch) -> Resolutions:
    """解きの回数を数え、code ごとの共有の置き場を空から始める(検の順に依らない)。"""
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
    assert run(with_handlers([fallback, first], alpha_then_beta())) == ("one:1", "fallback:Beta")
    resolved_by_the_first = resolutions.count
    assert resolved_by_the_first >= 1, "1 つ目の閉包の註は解く"

    second = alpha_handler("two")
    assert second is not first and second.__wrapped__.__code__ is first.__wrapped__.__code__
    assert run(with_handlers([fallback, second], alpha_then_beta())) == ("two:1", "fallback:Beta")
    assert resolutions.count == resolved_by_the_first, "同じ code で同じ註の閉包を 2 回目に据える時は解き直さない"
    assert handler_spec(second).effect_types == (Alpha,)


def test_b_closures_of_one_code_that_admit_different_types_do_not_mix(resolutions: Resolutions) -> None:
    alphas = kind_handler(Alpha, "alphas")
    betas = kind_handler(Beta, "betas")
    assert alphas.__wrapped__.__code__ is betas.__wrapped__.__code__

    assert run(with_handlers([fallback, alphas], alpha_then_beta())) == ("alphas:1", "fallback:Beta")
    # 同じ code の 2 つ目は Beta だけを受ける — Alpha の答えを借りると Beta が素通りして fallback に落ち、Alpha が入ってくる
    assert run(with_handlers([fallback, betas], alpha_then_beta())) == ("fallback:Alpha", "betas:2")
    assert handler_spec(alphas).effect_types == (Alpha,)
    assert handler_spec(betas).effect_types == (Beta,)
    # 1 つ目に戻っても 2 つ目の答えを借りない
    assert run(with_handlers([fallback, kind_handler(Alpha, "again")], alpha_then_beta())) == ("again:1", "fallback:Beta")


def test_b_a_union_annotation_shares_only_when_its_members_are_the_same(resolutions: Resolutions) -> None:
    both = kind_handler(Alpha | Beta, "both")
    assert run(with_handlers([fallback, both], alpha_then_beta())) == ("both:1", "both:2")
    resolved = resolutions.count
    assert run(with_handlers([fallback, kind_handler(Alpha | Beta, "again")], alpha_then_beta())) == ("again:1", "again:2")
    assert resolutions.count == resolved, "評価の度に新しい Alpha | Beta のオブジェクトでも、同じ型の組なら解き直さない"
    assert run(with_handlers([fallback, kind_handler(Beta, "betas")], alpha_then_beta())) == ("fallback:Alpha", "betas:2")


def test_b_the_passed_effects_of_closures_of_one_code_do_not_mix() -> None:
    def fence(passable: type) -> Callable[..., object]:
        """柵の形の handler — 番の型 passable は閉包ごとに違う。"""

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
    assert run(with_handlers([fallback, alphas], alpha_then_beta())) == ("fallback:Alpha", "fenced:Beta")
    assert run(with_handlers([fallback, betas], alpha_then_beta())) == ("fenced:Alpha", "fallback:Beta")


def test_c_a_handler_of_another_code_is_resolved_on_its_own(resolutions: Resolutions) -> None:
    assert run(with_handlers([fallback, alpha_handler("one")], alpha_then_beta())) == ("one:1", "fallback:Beta")
    resolved = resolutions.count
    assert run(with_handlers([fallback, other_alpha_handler("x")], alpha_then_beta())) == ("other-x:1", "fallback:Beta")
    assert resolutions.count == resolved + 1, "別の code は同じ註でも別に解く"


def test_d_the_shared_resolution_goes_with_its_code(resolutions: Resolutions) -> None:
    # 素の関数の handler(@do は code を自分の解析の cache で生かし続けるので、消える code の例は素の関数で作る)
    namespace: dict[str, object] = {"Resume": Resume, "Alpha": Alpha}
    exec(
        compile("def make():\n    def handle(effect: Alpha, k):\n        return Resume(k, effect.value)\n    return handle\n",
                "<handler made at run time>", "exec"),
        namespace,
    )
    handler = namespace["make"]()
    assert handler_spec(handler).effect_types == (Alpha,)
    assert len(_effect_types._SHARED_BY_CODE) == 1, "code 1 つに 1 行"
    del handler, namespace
    gc.collect()
    assert _effect_types._SHARED_BY_CODE == {}, "code が消えたら行も消える — 実行中に作る code が際限なく積もらない"
