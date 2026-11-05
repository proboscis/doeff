"""Failure cases for when the VM remembers, per effect type, the verdict to skip a handler based on its type annotation.

For handlers whose annotated type is a class made by `type` itself, the VM remembers per type whether "this handler accepts this effect type",
and does not call isinstance for the next effect of the same type. The shapes where the remembered verdict goes stale or where a per-instance answer is turned into a per-type answer are
checked one by one here:

- Change the effect type's inheritance (`__bases__`) later -> do not use the remembered verdict; judge by the new inheritance (the type's version tag changes)
- The annotated type is an ABC and something is registered later -> ABC isinstance is not decided by the type alone, so do not remember
- The effect type has `__class__` and each instance claims a different type -> not decided by the type alone, so do not remember
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
