"""出来事の送り出し(``notice_events_handler``)が、経路に無い発行と、送るだけの時の出来事の待ちで doeff-vm の歩数を使わない検
(作業ボードの card ki-3724ab2e9a0f・通過点 U)。

送り出しの body の handler は ``Publish`` と ``WaitForEvent`` / ``WaitForEvents`` を型で受け、経路に在るかを受けた後に見ていた。
経路に無い発行は外へ渡すだけ、送るだけの送り出し(読む channel が無い)の待ちは ``SourceFailed`` を足して外へ渡すだけだが、どちらも
毎回呼ばれる。agora の模擬の検(test_whole)では ``PROFILE-EVENT-SENDER`` 1 つが関係の無い発行 96 回で 594 歩を使い、送るだけの
送り出し 11 種の待ちの受けが計 683 回あった(agora-redesign #3766 の 2026-10-09 の記録)。送り出しを足すたびに、歩数の宣言を持つ
検がこの分だけ宣言を越える。

宣言: 送り出しを置いた組み立てと置かない組み立ての歩数の差は、経路に無い発行の数と(送るだけの時は)待ちの数で増えない。
bus は購読者の列を持つ ``subscribed_event_handler``(body が出した出来事は自分の列にも積まれる)なので、body は出してから待つだけで
task を起こさない — task を起こすと handler の列を task に被せ直す歩が混ざる。
"""

from dataclasses import dataclass

from doeff_core_effects.scheduler import _read_vm_steps, scheduled
from doeff_events import (
    Drop,
    EventBus,
    NoticeRoute,
    NoticeSent,
    Publish,
    WaitForEvent,
    WaitForEvents,
    notice_events_handler,
    subscribed_event_handler,
)
from doeff_events.effects import PublishEffect
from doeff_events.handlers.memory_notices import MemoryBroker, memory_notice_handler

from doeff import EffectGenerator, Program, do, run, with_handlers
from doeff.program import ProgramHandler

PATIENCE_SECONDS = 30.0
CHANNEL = "sender-steps"


@dataclass(frozen=True)
class Routed:
    """経路に在る出来事(送り出しが知らせに変える)。"""

    n: int


@dataclass(frozen=True)
class Local:
    """経路に無い出来事(process の中の bus だけを通る)。"""

    n: int


def _encoded(event: object) -> str:
    """経路の型の出来事の綴り(経路の表は ``NoticeRoute[object]`` の組で渡すので、受けるのは object)。"""
    assert isinstance(event, Routed), event
    return str(event.n)


SENDS: "tuple[NoticeRoute[object], ...]" = (
    NoticeRoute(
        event_type=Routed,
        wire_name="routed",
        channel=lambda _event: CHANNEL,
        encode=_encoded,
        decode=lambda body: Routed(n=int(body)),
        when_unsent=Drop(),
    ),
)
READS: "tuple[NoticeRoute[object], ...]" = (
    NoticeRoute(
        event_type=Routed,
        wire_name="routed",
        channel=lambda _event: CHANNEL,
        encode=_encoded,
        decode=lambda body: Routed(n=int(body)),
        when_unsent=Drop(),
        reads=(CHANNEL,),
    ),
)


def _steps_of(program: Program[object]) -> int:
    """``program`` を scheduler の下で走らせた doeff-vm の歩数。"""
    before = _read_vm_steps()
    run(scheduled(program))
    return _read_vm_steps() - before


@do
def _publishes(count: int) -> EffectGenerator[None]:
    """body: 経路に無い出来事を ``count`` 回出す。"""
    for n in range(count):
        yield Publish(Local(n))


@do
def _waits(count: int, many: bool) -> EffectGenerator[None]:
    """body: 経路に無い出来事を出して自分の列から待つことを ``count`` 回(``many`` なら ``WaitForEvents``)。"""
    for n in range(count):
        yield Publish(Local(n))
        if many:
            yield WaitForEvents(Local)
        else:
            yield WaitForEvent(Local)


def _composed(body: Program[object], *inner: ProgramHandler) -> Program[object]:
    """新しい bus の購読者 1 人の下に ``inner``(外が先)を並べて body を包む。"""
    return with_handlers([subscribed_event_handler(EventBus(), "body", (Local,)), *inner], body)


def _sender_steps(body: Program[None]) -> int:
    """送るだけの送り出しを body に被せた時に増える歩数(置かない同じ組み立てとの差)。"""
    sender = notice_events_handler("steps-sender", SENDS, PATIENCE_SECONDS)
    return _steps_of(_composed(body, sender)) - _steps_of(_composed(body))


def _reader_steps(body: Program[None]) -> int:
    """読む送り出し(channel を購読する)を body に被せた時に増える歩数(置かない同じ組み立てとの差)。"""
    reader = notice_events_handler("steps-reader", READS, PATIENCE_SECONDS)
    return _steps_of(_composed(body, memory_notice_handler(MemoryBroker()), reader)) - _steps_of(
        _composed(body, memory_notice_handler(MemoryBroker()))
    )


def test_a_sender_spends_no_step_on_events_it_does_not_route() -> None:
    assert _sender_steps(_publishes(10)) == _sender_steps(_publishes(0))


def test_a_reading_source_spends_no_step_on_events_it_does_not_route() -> None:
    assert _reader_steps(_publishes(10)) == _reader_steps(_publishes(0))


def test_a_sender_that_reads_nothing_spends_no_step_on_the_bodys_waits() -> None:
    assert _sender_steps(_waits(10, many=False)) == _sender_steps(_waits(0, many=False))
    assert _sender_steps(_waits(10, many=True)) == _sender_steps(_waits(0, many=True))


@do
def _sends_directly() -> EffectGenerator[object]:
    """body: 経路に在る出来事を ``PublishEffect`` を直に作って出す(doeff-records の memory の源が作る形)。"""
    answer: object = yield PublishEffect(Routed(1))
    return answer


def test_a_routed_event_made_as_publish_effect_is_still_sent() -> None:
    # 送り出しが受けるのは経路の型の Publish の class だけ — 直に作った値もその class になるので、外へ漏れずに知らせになる。
    sender = notice_events_handler("steps-sender", SENDS, PATIENCE_SECONDS)
    answer = run(
        scheduled(_composed(_sends_directly(), memory_notice_handler(MemoryBroker()), sender))
    )
    assert answer == NoticeSent(receivers=0)
