"""VM が型の注釈で handler を飛ばす答えを effect の型ごとに覚える時の失敗ケース(card ki-65239b222437)。

VM は、注釈の型が `type` そのものの作った class だけの handler について、「この effect の型を受けるか」を型ごとに覚え、
次の同じ型の effect では isinstance を呼ばない。覚えた答えが古くなる・instance ごとに違う答えを型の答えにしてしまう形を、
ここで 1 つずつ確かめる:

- effect の型の継承(`__bases__`)を後から変える → 覚えた答えを使わず、新しい継承で判じる(型の版の印が変わる)
- 注釈の型が ABC で、後から register する → ABC の isinstance は型だけで決まらないので覚えない
- effect の型が `__class__` を持ち、instance ごとに違う型を名乗る → 型だけで決まらないので覚えない
"""

from abc import ABC
from dataclasses import dataclass

from doeff import Effect, EffectBase, Resume, do, handler, run, with_handlers


class MarkA:
    pass


class MarkB:
    pass


@handler
def fallback(effect: Effect, k):
    return (yield Resume(k, "fallback"))


@handler
def takes_b(effect: MarkB, k):
    return (yield Resume(k, "b"))


def test_changed_bases_are_read_again() -> None:
    @dataclass(frozen=True)
    class Moving(EffectBase, MarkA):
        pass

    @do
    def body():
        before = yield Moving()
        Moving.__bases__ = (EffectBase, MarkB)
        after = yield Moving()
        return before, after

    assert run(with_handlers([fallback, takes_b], body())) == ("fallback", "b")


def test_abc_registered_later_is_not_kept_per_type() -> None:
    class Marker(ABC):
        pass

    @handler
    def takes_marker(effect: Marker, k):
        return (yield Resume(k, "marker"))

    @dataclass(frozen=True)
    class Plain(EffectBase):
        pass

    @do
    def body():
        before = yield Plain()
        Marker.register(Plain)
        after = yield Plain()
        return before, after

    assert run(with_handlers([fallback, takes_marker], body())) == ("fallback", "marker")


def test_instance_that_names_its_own_class_is_judged_per_instance() -> None:
    @dataclass(frozen=True)
    class Claims(EffectBase):
        claimed: type

        @property
        def __class__(self):  # isinstance falls back to __class__ when the type is not a subclass
            return self.claimed

    @do
    def body():
        first = yield Claims(MarkB)
        second = yield Claims(MarkA)
        third = yield Claims(MarkB)
        return first, second, third

    assert run(with_handlers([fallback, takes_b], body())) == ("b", "fallback", "b")


def test_same_type_keeps_its_answer_across_handlers() -> None:
    @dataclass(frozen=True)
    class OnlyA(EffectBase, MarkA):
        pass

    @dataclass(frozen=True)
    class OnlyB(EffectBase, MarkB):
        pass

    @do
    def body():
        return ((yield OnlyA()), (yield OnlyB()), (yield OnlyA()), (yield OnlyB()))

    assert run(with_handlers([fallback, takes_b], body())) == ("fallback", "b", "fallback", "b")
