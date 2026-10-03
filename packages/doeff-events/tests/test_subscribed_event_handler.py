"""memory の購読者ごとの列(subscribed_event_handler)の検 — 不変条件は event_signal_invariants の関数を memory の
組み立て方で呼ぶ(agora-redesign #3075)。"""

from functools import partial

import pytest
from doeff_core_effects.scheduler import Promise, SchedulerDeadlockError
from doeff_events import EventBus, event_handler, subscribed_event_handler
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

from doeff.program import ProgramHandler


def memory_world() -> SignalWorld:
    """1 つの世界 = 新しい ``EventBus`` 1 つ。"""
    return SignalWorld(subscribe=partial(subscribed_event_handler, EventBus()), run=run_scheduled)


def legacy_world() -> SignalWorld:
    """元の ``event_handler()`` を 1 つ作り、全購読者が同じものを使う(購読者の名と型は見ない)。"""
    shared = event_handler()

    def subscribe(subscriber: str, event_types: tuple[type, ...] = (), /) -> ProgramHandler:
        """購読者が誰でも同じ handler を返す — 元の handler に購読の概念が無いことをそのまま写す。"""
        return shared

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

    assert queue.offer(Changed("a")) is first
    assert queue.offer(Unrelated("x")) is None
    assert queue.offer(Changed("b")) is second
    assert queue.offer(Changed("c")) is None
    assert queue.offer(Changed("d")) is None
    assert queue.take((Changed,)) == Changed("c")
    assert queue.take((Changed,)) == Changed("d")
    assert queue.take((Changed,)) is EMPTY


def test_queue_take_skips_types_not_wanted() -> None:
    queue = SubscriberQueue((Changed, Unrelated))
    assert queue.offer(Unrelated("x")) is None
    assert queue.offer(Changed("a")) is None

    assert queue.take((Changed,)) == Changed("a")
    assert queue.take((Changed,)) is EMPTY
    assert queue.take((Unrelated,)) == Unrelated("x")


def test_queue_removed_waiter_is_not_woken() -> None:
    queue = SubscriberQueue((Changed,))
    promise = Promise[object](1)
    queue.add_waiter((Changed,), promise)
    queue.remove_waiter(promise)

    assert queue.offer(Changed("a")) is None
    assert queue.take((Changed,)) == Changed("a")


def test_wait_for_subtype_of_subscribed_type_is_inside() -> None:
    class Narrow(Changed):
        pass

    queue = SubscriberQueue((Changed,))
    assert queue.outside((Narrow, Changed)) == ()
    assert queue.outside((Unrelated, object)) == (Unrelated, object)
