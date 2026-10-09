"""``WaitForEvents`` の検 — 1 つ以上届くまで待ち、届いた時点で自分の列に在る当たる合図を全部、来た順の tuple で答える。

``WaitForEvent`` が 1 つずつ答えると、同じ刻に届いた複数の知らせを受け手が 1 つずつ畳み、途中の姿を外へ送る。
``WaitForEvents`` は届いている物を全部 1 回で受ける。

- 待つ前に発した 2 つを、来た順の 2 つの組で受ける(``subscribed_event_handler``)。
- 待っている間に発した 1 つを、1 つの組で受ける。
- 購読の型の外を待つと ``ValueError``(``WaitForEvent`` と同じ文)。
- 列を持たない ``event_handler`` は、届いた 1 つを 1 つの組で答える。
- ``notice_events_handler`` の包みの中: 自分の源の ``SourceFailed`` で例外になり、別の源の ``SourceFailed`` は組から除く。待ちに源の失敗を
  足すのは channel を読む(源の task を持つ)包みだけで、送るだけの包みは待ちを受けない(作業ボードの card ki-3724ab2e9a0f)。
"""

import asyncio
from dataclasses import dataclass

import pytest
from doeff_core_effects import Await
from doeff_core_effects.scheduler import Spawn, Wait
from doeff_events import (
    Drop,
    EventBus,
    MemoryBroker,
    NoticeRoute,
    Publish,
    SourceFailed,
    WaitForEvents,
    event_handler,
    memory_notice_handler,
    notice_events_handler,
    subscribed_event_handler,
)
from events_test_support import run_scheduled

from doeff import EffectGenerator, Program, do


@dataclass(frozen=True)
class Rang:
    """受け手の待つ合図(``keys`` を持たないので列の中でまとまらない)。"""

    room: str


@dataclass(frozen=True)
class Unrelated:
    """受け手の購読の型の外の合図。"""

    note: str


@dataclass(frozen=True)
class Heard:
    """包みが channel から受ける合図(この検では誰も送らない — 包みに源の task を持たせるためだけの経路の型)。"""

    note: str


HEARD_CHANNEL = "heard"


def _heard_encoded(event: object) -> str:
    """``Heard`` の綴り(経路の表は ``NoticeRoute[object]`` の組で渡すので、受けるのは object)。"""
    assert isinstance(event, Heard), event
    return event.note


READS_HEARD: "tuple[NoticeRoute[object], ...]" = (
    NoticeRoute(
        event_type=Heard,
        wire_name="heard",
        channel=lambda _event: HEARD_CHANNEL,
        encode=_heard_encoded,
        decode=Heard,
        when_unsent=Drop(),
        reads=(HEARD_CHANNEL,),
    ),
)


class SourceBrokeError(RuntimeError):
    """源の task が落ちた時の例外(``SourceFailed`` が運ぶ)。"""


@do
def _publish_all(events: tuple[object, ...]) -> EffectGenerator[None]:
    """書き手: ``events`` を順に発する。"""
    for event in events:
        yield Publish(event)


@do
def _publish_later(events: tuple[object, ...]) -> EffectGenerator[None]:
    """書き手: 受け手が待ちに入るまで外の時間を少し待ってから、``events`` を順に発する。"""
    _ = yield Await(asyncio.sleep(0.01))
    yield _publish_all(events)


@do
def _wait_rang() -> EffectGenerator[tuple[object, ...]]:
    """受け手: ``Rang`` を ``WaitForEvents`` の 1 回で受ける。"""
    came: tuple[object, ...] = yield WaitForEvents(Rang)
    return came


def _received_after(
    published: tuple[object, ...], body: Program[tuple[object, ...]]
) -> tuple[object, ...]:
    """書き手が ``published`` を発し終えた後で、購読者 listener(``Rang`` を購読)が ``body`` を走らせた答え。"""
    bus = EventBus()
    receiving = subscribed_event_handler(bus, "listener", (Rang,))
    sending = subscribed_event_handler(bus, "writer")

    @do
    def main() -> EffectGenerator[tuple[object, ...]]:
        """受け手の購読を始めてから書き手を走らせ終え、その後で受け手を走らせる。"""
        yield Wait((yield Spawn(sending(_publish_all(published)))))
        answer: tuple[object, ...] = yield receiving(body)
        return answer

    return run_scheduled(main())


def test_wait_for_events_answers_every_queued_signal_in_arrival_order() -> None:
    received = _received_after((Rang("a"), Rang("b")), _wait_rang())
    assert received == (Rang("a"), Rang("b")), received


def test_wait_for_events_answers_the_one_signal_published_while_it_waits() -> None:
    bus = EventBus()
    receiving = subscribed_event_handler(bus, "listener", (Rang,))
    sending = subscribed_event_handler(bus, "writer")

    @do
    def main() -> EffectGenerator[tuple[object, ...]]:
        """受け手が待っている間に書き手が 1 つ発する。"""
        writer = yield Spawn(sending(_publish_later((Rang("a"),))))
        answer: tuple[object, ...] = yield receiving(_wait_rang())
        yield Wait(writer)
        return answer

    received = run_scheduled(main())
    assert received == (Rang("a"),), received


def test_wait_for_events_outside_the_subscription_is_rejected_like_wait_for_event() -> None:
    @do
    def wait_unrelated() -> EffectGenerator[tuple[object, ...]]:
        """購読の型の外の合図を待つ(配線の誤り)。"""
        came: tuple[object, ...] = yield WaitForEvents(Unrelated)
        return came

    with pytest.raises(ValueError, match=r"購読者 'listener' は購読の型の外を待った: Unrelated"):
        _received_after((), wait_unrelated())


def test_the_handler_without_queues_answers_the_one_signal_as_a_tuple() -> None:
    @do
    def main() -> EffectGenerator[tuple[object, ...]]:
        """受け手が待っている間に 1 つ発する(列を持たない handler は待ち手にだけ渡す)。"""
        writer = yield Spawn(_publish_later((Rang("a"),)))
        answer: tuple[object, ...] = yield _wait_rang()
        yield Wait(writer)
        return answer

    received = run_scheduled(event_handler()(main()))
    assert received == (Rang("a"),), received


def _received_in_notice_wrapper(published: tuple[object, ...]) -> tuple[object, ...]:
    """``notice_events_handler``(源の名 mine・channel を 1 つ読む)の包みの中で ``WaitForEvents(Rang)`` を 1 回した答え。"""
    reader = notice_events_handler("mine", READS_HEARD, 30.0)
    return _received_after(published, memory_notice_handler(MemoryBroker())(reader(_wait_rang())))


def test_wait_for_events_in_the_notice_wrapper_drops_another_sources_failure() -> None:
    other = SourceFailed(source="other", error=SourceBrokeError("other"))
    received = _received_in_notice_wrapper((other, Rang("a"), Rang("b")))
    assert received == (Rang("a"), Rang("b")), received


def test_wait_for_events_in_the_notice_wrapper_raises_its_own_sources_failure() -> None:
    mine = SourceFailed(source="mine", error=SourceBrokeError("mine"))
    with pytest.raises(SourceBrokeError, match="mine"):
        _received_in_notice_wrapper((Rang("a"), mine))
