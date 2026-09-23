"""handler の effect の引数の型注記で、VM が effect を振り分ける(SPEC-WITHHANDLER-TYPE-FILTER)。

実測(agora-controllers の実験 2026-09-23): handler の第 1 引数の型注記で振り分ける仕組みが、
@do で包んだ handler では効かなかった — 実際には VM のどこにも実装が無く、どの handler も全部の
effect を受けていた(PromptBoundary.types は常に None)。docs/llm_unified_effects.md は効くと書いていた。

型は isinstance の意味で当てるので、親の型(書き込みの印の型など)で注記した handler は、後から
足した子の型もそのまま捕まえる(不具合 4: 書き込みの guard を一覧で持つと 8 種目が素通りした)。
"""

import functools
import warnings
from dataclasses import dataclass
from typing import Annotated, Any, Generic, TypeVar, Union

import pytest
from doeff_core_effects.scheduler import scheduled
from doeff_vm._effect_types import handler_effect_types

from doeff import Effect, EffectBase, Pass, Resume, Spawn, Wait, do, run, with_handlers

T = TypeVar("T")


@dataclass(frozen=True)
class Alpha(EffectBase):
    value: str


@dataclass(frozen=True)
class Beta(EffectBase):
    value: str


class WriteMarker:
    """書き込みの effect の印(EffectBase の子でなくてよい — isinstance で当たる)。"""


@dataclass(frozen=True)
class WriteRow(EffectBase, WriteMarker):
    key: str


class GenericWrite(EffectBase, Generic[T]):
    pass


@dataclass(frozen=True)
class WriteFlag(GenericWrite[bool]):
    key: str


calls: list[str] = []


@do
def alpha_only(effect: Alpha, k):
    calls.append(f"alpha_only:{type(effect).__name__}")
    return (yield Resume(k, f"alpha:{effect.value}"))


@do
def catch_all(effect: Effect, k):
    calls.append(f"catch_all:{type(effect).__name__}")
    return (yield Resume(k, f"fallback:{type(effect).__name__}:{effect.value}"))


def _stack(inner, program):
    return with_handlers([catch_all, inner], program)


@do
def alpha_then_beta():
    a = yield Alpha("1")
    b = yield Beta("2")
    a2 = yield Alpha("3")
    return (a, b, a2)


@pytest.fixture(autouse=True)
def _reset_calls():
    calls.clear()


def test_typed_do_handler_is_not_called_for_other_effects() -> None:
    assert run(_stack(alpha_only, alpha_then_beta())) == ("alpha:1", "fallback:Beta:2", "alpha:3")
    assert calls == ["alpha_only:Alpha", "catch_all:Beta", "alpha_only:Alpha"]


def test_typed_plain_function_handler() -> None:
    def alpha_plain(effect: Alpha, k):
        calls.append("alpha_plain")
        return Resume(k, f"plain:{effect.value}")

    assert run(_stack(alpha_plain, alpha_then_beta())) == ("plain:1", "fallback:Beta:2", "plain:3")
    assert calls.count("alpha_plain") == 2


def test_union_annotation_admits_each_member() -> None:
    @do
    def alpha_or_beta(effect: Alpha | Beta, k):
        return (yield Resume(k, f"both:{effect.value}"))

    assert run(_stack(alpha_or_beta, alpha_then_beta())) == ("both:1", "both:2", "both:3")


def test_partial_bound_method_and_callable_instance_use_the_effect_parameter() -> None:
    @do
    def prefixed(prefix: str, effect: Alpha, k):
        return (yield Resume(k, f"{prefix}{effect.value}"))

    class Holder:
        @do
        def method(self, effect: Alpha, k):
            return (yield Resume(k, f"method:{effect.value}"))

        def __call__(self, effect: Alpha, k):
            return Resume(k, f"instance:{effect.value}")

    expected_tail = "fallback:Beta:2"
    assert run(_stack(functools.partial(prefixed, "p:"), alpha_then_beta()))[1] == expected_tail
    assert run(_stack(Holder().method, alpha_then_beta()))[1] == expected_tail
    assert run(_stack(Holder(), alpha_then_beta())) == ("instance:1", expected_tail, "instance:3")


def test_parent_type_annotation_catches_subclasses_defined_later() -> None:
    @do
    def refuse_writes(effect: WriteMarker, k):
        raise PermissionError(f"read-only: {type(effect).__name__}")
        yield

    @dataclass(frozen=True)
    class DeleteRow(EffectBase, WriteMarker):  # 8 種目: guard を直さずに断られる
        key: str

    @do
    def reads_then_deletes():
        a = yield Alpha("read")
        yield DeleteRow("row")
        return a

    with pytest.raises(PermissionError, match="read-only: DeleteRow"):
        run(_stack(refuse_writes, reads_then_deletes()))
    assert calls == ["catch_all:Alpha"]


def test_parameterised_generic_parent_annotation() -> None:
    @do
    def flags(effect: GenericWrite[Any], k):
        return (yield Resume(k, f"flag:{effect.key}"))

    @do
    def body():
        return ((yield WriteFlag("f")), (yield Alpha("a")))

    assert run(_stack(flags, body())) == ("flag:f", "fallback:Alpha:a")


def test_skipped_handler_between_pass_chain() -> None:
    @do
    def passes_everything(effect: Effect, k):
        calls.append("passes")
        yield Pass(effect, k)

    program = with_handlers([catch_all, alpha_only, passes_everything], alpha_then_beta())
    assert run(program) == ("alpha:1", "fallback:Beta:2", "alpha:3")
    assert calls == [
        "passes", "alpha_only:Alpha",
        "passes", "catch_all:Beta",
        "passes", "alpha_only:Alpha",
    ]


def test_typed_handler_is_reinstalled_with_its_filter_in_spawned_tasks() -> None:
    @do
    def spawner():
        task = yield Spawn(alpha_then_beta())
        return (yield Wait(task))

    @do
    def beta_fallback(effect: Beta, k):
        return (yield Resume(k, f"fallback:Beta:{effect.value}"))

    # scheduled の Spawn / Wait は型の合わない 2 つの handler を素通りして scheduler へ届く。
    program = scheduled(with_handlers([beta_fallback, alpha_only], spawner()))
    assert run(program) == ("alpha:1", "fallback:Beta:2", "alpha:3")


def test_unhandled_effect_still_raises_when_only_typed_handlers_remain() -> None:
    with pytest.raises(Exception, match="Beta"):
        run(with_handlers([alpha_only], alpha_then_beta()))


# --- the annotation → type tuple rule -----------------------------------------------------------


def _h(annotation):
    def handler(effect, k):
        return Resume(k, None)

    handler.__annotations__ = {"effect": annotation}
    return handler


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        (Alpha, (Alpha,)),
        (Alpha | Beta, (Alpha, Beta)),
        (Union[Alpha, Beta], (Alpha, Beta)),  # noqa: UP007 - the typing.Union spelling is part of the rule
        (Annotated[Alpha, "doc"], (Alpha,)),
        (GenericWrite[bool], (GenericWrite,)),
        (WriteMarker, (WriteMarker,)),
        ("Alpha", (Alpha,)),
        (Effect, None),
        (EffectBase, None),
        (Any, None),
        (object, None),
        (Alpha | EffectBase, None),
        (T, None),
    ],
)
def test_handler_effect_types_rule(annotation, expected) -> None:
    assert handler_effect_types(_h(annotation)) == expected


def test_no_annotation_means_every_effect() -> None:
    def handler(effect, k):
        return Resume(k, None)

    assert handler_effect_types(handler) is None


def test_unresolvable_annotation_warns_and_admits_every_effect() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert handler_effect_types(_h("NotDefinedAnywhere")) is None
    assert any("cannot be evaluated" in str(w.message) for w in caught)
