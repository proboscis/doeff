"""memory の購読者ごとの列(subscribed_event_handler)の検 — 不変条件は event_signal_invariants の関数を memory の
組み立て方で呼ぶ(agora-redesign #3075)。"""

from collections.abc import Hashable
from dataclasses import dataclass

import pytest
from doeff_core_effects.scheduler import Promise, SchedulerDeadlockError, Spawn, Wait
from doeff_events import (
    EventBus,
    Publish,
    TimerFired,
    WaitForEvent,
    event_handler,
    subscribed_event_handler,
)
from doeff_events.handlers.memory import EMPTY, SubscriberQueue
from event_signal_invariants import (
    INVARIANTS,
    Changed,
    Invariant,
    SignalWorld,
    Unrelated,
    run_read_process_wait,
)
from events_test_support import run_scheduled

from doeff import EffectGenerator, Program, Pure, do
from doeff.program import ProgramHandler


def memory_world() -> SignalWorld:
    """1 つの世界 = 新しい ``EventBus`` 1 つ。"""
    bus = EventBus()

    def subscribe(subscriber: str, event_types: tuple[type, ...] = (), /) -> Program[ProgramHandler]:
        """呼んだ時に ``bus`` へ購読を始め、組み立てた handler をそのまま答える Program にする(memory の組み立ては effect を出さない)。"""
        return Pure(subscribed_event_handler(bus, subscriber, event_types))

    return SignalWorld(subscribe=subscribe, run=run_scheduled)


def legacy_world() -> SignalWorld:
    """元の ``event_handler()`` を 1 つ作り、全購読者が同じものを使う(購読者の名と型は見ない)。"""
    shared = event_handler()

    def subscribe(subscriber: str, event_types: tuple[type, ...] = (), /) -> Program[ProgramHandler]:
        """購読者が誰でも同じ handler を返す — 元の handler に購読の概念が無いことをそのまま写す。"""
        return Pure(shared)

    return SignalWorld(subscribe=subscribe, run=run_scheduled)


@pytest.mark.parametrize("invariant", INVARIANTS, ids=lambda invariant: invariant.__name__)
def test_memory_subscribed_handler_keeps_invariant(invariant: Invariant) -> None:
    """共有の不変条件を memory の組み立て方で 1 つずつ確かめる。"""
    invariant(memory_world())


def test_legacy_event_handler_drops_signal_between_read_and_wait() -> None:
    # 意味を変えていない証: 元の event_handler は待ち手の居ない間の合図を捨てるので、同じ筋書きで受け手が起こされない。
    with pytest.raises(SchedulerDeadlockError):
        run_read_process_wait(legacy_world())


def test_queue_wakes_waiters_in_arrival_order_and_keeps_order() -> None:
    queue = SubscriberQueue((Changed,))
    first, second = Promise[object](1), Promise[object](2)
    queue.add_waiter((Changed,), first)
    queue.add_waiter((Changed,), second)

    assert queue.offer(Changed(("a",))) is first
    assert queue.offer(Unrelated("x")) is None
    assert queue.offer(Changed(("b",))) is second
    assert queue.offer(Changed(("c",))) is None
    # 待ち手の居ない間の同じ型の合図は 1 つにまとまり、所は来た順のまま(agora-redesign #3079)。
    assert queue.offer(Changed(("d",))) is None
    assert queue.take((Changed,)) == Changed(("c", "d"))
    assert queue.take((Changed,)) is EMPTY


def test_queue_take_skips_types_not_wanted() -> None:
    queue = SubscriberQueue((Changed, Unrelated))
    assert queue.offer(Unrelated("x")) is None
    assert queue.offer(Changed(("a",))) is None

    assert queue.take((Changed,)) == Changed(("a",))
    assert queue.take((Changed,)) is EMPTY
    assert queue.take((Unrelated,)) == Unrelated("x")


def test_queue_removed_waiter_is_not_woken() -> None:
    queue = SubscriberQueue((Changed,))
    promise = Promise[object](1)
    queue.add_waiter((Changed,), promise)
    queue.remove_waiter(promise)

    assert queue.offer(Changed(("a",))) is None
    assert queue.take((Changed,)) == Changed(("a",))


def test_wait_for_subtype_of_subscribed_type_is_inside() -> None:
    class Narrow(Changed):
        pass

    queue = SubscriberQueue((Changed,))
    assert queue.outside((Narrow, Changed)) == ()
    assert queue.outside((Unrelated, object)) == (Unrelated, object)


# 待ち手の居ない間に溜まった同じ型の合図を 1 つにまとめる(agora-redesign #3079・設計 #3072 の 3 節 5)。
# offer が常に末尾に積む形に戻すと、まとまる筋書き(1 本目と 3 本目)が赤になる。


@dataclass(frozen=True)
class Moved:
    """``Changed`` と別の型の、所 ``keys`` の合図。"""

    keys: tuple[Hashable, ...]


@dataclass(frozen=True)
class EndOfQueue:
    """受け手が自分の列の終わりの印に発する合図(``keys`` を持たないのでまとまらない)。"""


def received_after_quiet_spell(published: tuple[object, ...], waited: tuple[type, ...]) -> tuple[object, ...]:
    """受け手が待っていない間に書き手が ``published`` を発し終え、その後で受け手が列を空になるまで受けた合図の列。

    受け手は ``WaitForEvent`` を 1 回ずつ重ね、列の終わりの印(自分で末尾に発した ``EndOfQueue``)で止まる。
    """
    bus = EventBus()
    receiving = subscribed_event_handler(bus, "worker", (*waited, EndOfQueue))
    sending = subscribed_event_handler(bus, "writer")

    @do
    def publish_all() -> EffectGenerator[None]:
        """書き手: ``published`` を順に発する(受け手はまだ待っていない)。"""
        for event in published:
            yield Publish(event)

    @do
    def drain(received: tuple[object, ...]) -> EffectGenerator[tuple[object, ...]]:
        """受け手: 次の合図を 1 つ受け、列の終わりの印までの合図を来た順に返す。"""
        event: object = yield WaitForEvent(*waited, EndOfQueue)
        if isinstance(event, EndOfQueue):
            return received
        return (yield drain((*received, event)))

    @do
    def receive_all() -> EffectGenerator[tuple[object, ...]]:
        """受け手: 列の終わりの印を自分の列の末尾に発してから、印まで受ける。"""
        yield Publish(EndOfQueue())
        return (yield drain(()))

    @do
    def main() -> EffectGenerator[tuple[object, ...]]:
        """書き手が発し終えるのを待ってから、受け手を走らせる。"""
        yield Wait((yield Spawn(sending(publish_all()))))
        return (yield receiving(receive_all()))

    result = run_scheduled(main())
    assert isinstance(result, tuple), result
    return result


def test_same_type_signals_while_no_one_waits_are_received_in_one_wait() -> None:
    received = received_after_quiet_spell(
        (Changed(("a",)), Changed(("b", "a")), Changed(("c",))), (Changed,)
    )
    # 3 つの合図が次の WaitForEvent の 1 回で受かり、所は来た順に重なりを除いて合わさる。
    assert received == (Changed(("a", "b", "c")),), received


def test_timer_fired_is_received_per_tag() -> None:
    # keys を持たない TimerFired は tag ごとに意味が違うのでまとめない(同じ tag の 2 度目も別に受かる)。
    received = received_after_quiet_spell(
        (TimerFired("x"), TimerFired("y"), TimerFired("x")), (TimerFired,)
    )
    assert received == (TimerFired("x"), TimerFired("y"), TimerFired("x")), received


def test_signals_of_other_types_are_received_apart() -> None:
    received = received_after_quiet_spell(
        (Changed(("a",)), Moved(("a",)), Changed(("b",))), (Changed, Moved)
    )
    # 別の型はまとまらず、同じ型は先に来た方の位置でまとまる。
    assert received == (Changed(("a", "b")), Moved(("a",))), received


@dataclass(frozen=True)
class BoardChanged:
    """``keys`` の外にも欄を持つ合図。"""

    keys: tuple[Hashable, ...]
    board: str


@dataclass
class MutableChanged:
    """frozen でない、所 ``keys`` の合図。"""

    keys: tuple[Hashable, ...]


def test_only_frozen_keyed_signals_with_equal_other_fields_merge() -> None:
    queue = SubscriberQueue((BoardChanged, MutableChanged))
    for event in (
        BoardChanged(("a",), "left"),
        BoardChanged(("b",), "right"),
        BoardChanged(("c",), "left"),
        MutableChanged(("a",)),
        MutableChanged(("b",)),
    ):
        assert queue.offer(event) is None

    # keys の外の欄が違えば、まとめると片方の欄が消えるのでまとめない。frozen でない dataclass はまとめない。
    assert queue.take((BoardChanged,)) == BoardChanged(("a", "c"), "left")
    assert queue.take((BoardChanged,)) == BoardChanged(("b",), "right")
    assert queue.take((MutableChanged,)) == MutableChanged(("a",))
    assert queue.take((MutableChanged,)) == MutableChanged(("b",))
    assert queue.take((BoardChanged, MutableChanged)) is EMPTY
