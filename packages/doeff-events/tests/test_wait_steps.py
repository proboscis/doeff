"""購読者の列を持つ handler(``subscribed_event_handler``)で、待ってから起こされる ``WaitForEvent`` / ``WaitForEvents`` の doeff-vm の歩数の検。

``WaitForEvents`` を足した時(2ae3daeb7)に、2 つの待ちが共有する「列から取る・無ければ約束で待つ」を ``@do`` の下請けの Program にした。
下請けの Program は待ちのたびに 1 段多く回るので、起こされる待ち 1 回が 3 歩増え、agora の画面の検(test_screen_card_accept_replies)が
1 本で 573 歩増えて repo の歩の上限を越えた(日次 2026-10-07 08:00)。共有は歩を足さない形(ただの generator を ``yield from`` で委ねる)で書く。

比べる相手は列を持たない ``event_handler``(同じ待ちを handler の中でそのまま書く)。2 つの組み立ての違いは、購読者ごとの handler を待ち手の
task に 1 つ被せる 1 歩だけ(``event_handler`` は 1 つを全員で共有して外側に 1 度だけ被せる)。
"""

from dataclasses import dataclass

from doeff_core_effects.scheduler import Spawn, Wait, _read_vm_steps, scheduled

from doeff import do, run, with_handlers
from doeff_events import (
    EventBus,
    Publish,
    WaitForEvent,
    WaitForEvents,
    event_handler,
    subscribed_event_handler,
)

# 購読者ごとの handler を待ち手の task に被せる歩(event_handler の組み立てには無い)。
WAITER_HANDLER_STEPS = 1


@dataclass(frozen=True)
class Signal:
    """検の合図。"""

    n: int


def steps_of(program: object) -> int:
    """``program`` を scheduler の下で走らせた doeff-vm の歩数。"""
    before = _read_vm_steps()
    run(scheduled(program))
    return _read_vm_steps() - before


@do
def waiting(many: bool):
    """合図を 1 つ待つ(``many`` なら ``WaitForEvents``)。"""
    if many:
        got = yield WaitForEvents(Signal)
    else:
        got = yield WaitForEvent(Signal)
    return got


@do
def publishing():
    """合図を 1 つ出す。"""
    yield Publish(Signal(1))
    return None


def subscribed_woken_steps(many: bool) -> int:
    """購読者の列を持つ handler で、待ち手が先に待ち、別の task の合図で起こされる待ち 1 回の歩数(待ち手の無い同じ組み立てとの差)。"""

    @do
    def tasks(with_waiter: bool):
        bus = EventBus()
        waiter = with_handlers([subscribed_event_handler(bus, "w", (Signal,))], waiting(many))
        spawned = ((yield Spawn(waiter)),) if with_waiter else ()
        sender = yield Spawn(with_handlers([subscribed_event_handler(bus, "p", (Signal,))], publishing()))
        yield Wait(sender)
        for task in spawned:
            yield Wait(task)
        return None

    return steps_of(tasks(True)) - steps_of(tasks(False))


def shared_woken_steps() -> int:
    """列を持たない ``event_handler`` で、同じく待ってから起こされる待ち 1 回の歩数(待ち手の無い同じ組み立てとの差)。"""

    @do
    def tasks(with_waiter: bool):
        spawned = ((yield Spawn(waiting(False))),) if with_waiter else ()
        sender = yield Spawn(publishing())
        yield Wait(sender)
        for task in spawned:
            yield Wait(task)
        return None

    return steps_of(with_handlers([event_handler()], tasks(True))) - steps_of(
        with_handlers([event_handler()], tasks(False))
    )


def test_a_woken_wait_for_event_costs_no_more_than_the_shared_handler_and_the_waiters_own_handler() -> None:
    assert subscribed_woken_steps(many=False) == shared_woken_steps() + WAITER_HANDLER_STEPS


def test_a_woken_wait_for_events_costs_the_same_as_a_woken_wait_for_event() -> None:
    assert subscribed_woken_steps(many=True) == subscribed_woken_steps(many=False)
