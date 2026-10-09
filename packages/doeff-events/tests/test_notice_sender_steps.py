"""出来事の送り出し(``notice_events_handler``)が、経路に無い発行と、送るだけの時の出来事の待ちで doeff-vm の歩数を使わない検
(作業ボードの card ki-3724ab2e9a0f・通過点 U)。

送り出しの body の handler は ``Publish`` と ``WaitForEvent`` / ``WaitForEvents`` を型で受け、経路に在るかを受けた後に見ていた。
経路に無い発行は外へ渡すだけ(1 回 約 6 歩)、送るだけの送り出し(読む channel が無い)の待ちは ``SourceFailed`` を足して外へ渡すだけ
だが、どちらも毎回呼ばれる。agora の模擬の検(test_whole)では ``PROFILE-EVENT-SENDER`` 1 つが関係の無い発行 96 回で 594 歩を使い、
送るだけの送り出し 11 種の待ちの受けが計 683 回あった(agora-redesign #3766 の 2026-10-09 の記録)。送り出しを足すたびに、
歩数の宣言を持つ検がこの分だけ宣言を越える。

宣言: 送り出しを置いた組み立てと置かない組み立ての歩数の差は、経路に無い発行の数と(送るだけの時は)待ちの数で増えない。
"""

from dataclasses import dataclass

from doeff_core_effects.scheduler import Spawn, Wait, _read_vm_steps, scheduled
from doeff_events import Drop, NoticeRoute, Publish, WaitForEvent, WaitForEvents, event_handler, notice_events_handler
from doeff_events.handlers.memory_notices import MemoryBroker, memory_notice_handler

from doeff import EffectGenerator, Program, do, run, with_handlers

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


def _channel(event: Routed) -> str:
    return CHANNEL


def _encoded(event: Routed) -> str:
    return str(event.n)


def _decoded(text: str) -> Routed:
    return Routed(n=int(text))


SENDS = (
    NoticeRoute(
        event_type=Routed, wire_name="routed", channel=_channel, encode=_encoded, decode=_decoded, when_unsent=Drop()
    ),
)
READS = (
    NoticeRoute(
        event_type=Routed,
        wire_name="routed",
        channel=_channel,
        encode=_encoded,
        decode=_decoded,
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
def _publishing(n: int) -> EffectGenerator[None]:
    """経路に無い出来事を 1 つ出す task。"""
    yield Publish(Local(n))


@do
def _publishes(count: int) -> EffectGenerator[None]:
    """body: 経路に無い出来事を ``count`` 回出す。"""
    for n in range(count):
        yield Publish(Local(n))


@do
def _waiting(many: bool) -> EffectGenerator[None]:
    """経路に無い出来事を 1 つ待つ task(``many`` なら ``WaitForEvents``)。"""
    if many:
        yield WaitForEvents(Local)
    else:
        yield WaitForEvent(Local)


@do
def _waits(count: int, many: bool) -> EffectGenerator[None]:
    """body: 経路に無い出来事を待つ task と出す task を ``count`` 組起こす(待つ方を先に起こす — 出す方が先に走ると待ちが永遠に来ない)。"""
    for n in range(count):
        waiter = yield Spawn(_waiting(many))
        sender = yield Spawn(_publishing(n))
        yield Wait(sender)
        yield Wait(waiter)


def _sender_steps(body: Program[None]) -> int:
    """送るだけの送り出しを body に被せた時に増える歩数(置かない同じ組み立てとの差)。"""
    sender = notice_events_handler("steps-sender", SENDS, PATIENCE_SECONDS)
    return _steps_of(with_handlers([event_handler(), sender], body)) - _steps_of(with_handlers([event_handler()], body))


def _reader_steps(body: Program[None]) -> int:
    """読む送り出し(channel を購読する)を body に被せた時に増える歩数(置かない同じ組み立てとの差)。"""

    def composed(with_reader: bool) -> Program[None]:
        reader = (notice_events_handler("steps-reader", READS, PATIENCE_SECONDS),) if with_reader else ()
        return with_handlers([event_handler(), memory_notice_handler(MemoryBroker()), *reader], body)

    return _steps_of(composed(True)) - _steps_of(composed(False))


def test_a_sender_spends_no_step_on_events_it_does_not_route() -> None:
    assert _sender_steps(_publishes(10)) == _sender_steps(_publishes(0))


def test_a_reading_source_spends_no_step_on_events_it_does_not_route() -> None:
    assert _reader_steps(_publishes(10)) == _reader_steps(_publishes(0))


def test_a_sender_that_reads_nothing_spends_no_step_on_the_bodys_waits() -> None:
    assert _sender_steps(_waits(10, many=False)) == _sender_steps(_waits(0, many=False))
    assert _sender_steps(_waits(10, many=True)) == _sender_steps(_waits(0, many=True))
