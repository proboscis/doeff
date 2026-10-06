"""源の包み(waits-beside-sources)が本体の ``WaitForEvents`` にも源の失敗の合図を足して待たせるかの検。

本体の ``WaitForEvent`` と同じ扱い: 自分の源の ``SourceFailed`` が組に在れば運ばれた例外で落ち、同じ bus の別の源の
``SourceFailed`` は(本体が ``SourceFailed`` を待っていなければ)組から除く。
"""

from dataclasses import dataclass

import hy  # noqa: F401  Hy の module を読むため
import pytest
from doeff_core_effects.scheduler import Spawn, Wait, scheduled
from doeff_events import EventBus, Publish, SourceFailed, WaitForEvents, subscribed_event_handler
from doeff_records.event_source import waits_beside_sources

from doeff import EffectGenerator, do, run


@dataclass(frozen=True)
class Rang:
    """本体の待つ合図(``keys`` を持たないので列の中でまとまらない)。"""

    room: str


class SourceBroke(RuntimeError):
    """源の task が落ちた時の例外(``SourceFailed`` が運ぶ)。"""


@do
def _publish_all(events: tuple[object, ...]) -> EffectGenerator[None]:
    """書き手: ``events`` を順に発する。"""
    for event in events:
        yield Publish(event)


@do
def _wait_rang() -> EffectGenerator[tuple[object, ...]]:
    """本体: ``Rang`` を ``WaitForEvents`` の 1 回で受ける。"""
    came: tuple[object, ...] = yield WaitForEvents(Rang)
    return came


def _received_beside_source(published: tuple[object, ...]) -> tuple[object, ...]:
    """書き手が ``published`` を発し終えた後で、源 mine の包みの中の本体が ``WaitForEvents(Rang)`` を 1 回した答え。"""
    bus = EventBus()
    receiving = subscribed_event_handler(bus, "mine", (Rang,))
    sending = subscribed_event_handler(bus, "writer")

    @do
    def main() -> EffectGenerator[tuple[object, ...]]:
        """受け手の購読を始めてから書き手を走らせ終え、その後で包んだ本体を走らせる。"""
        yield Wait((yield Spawn(sending(_publish_all(published)))))
        answer: tuple[object, ...] = yield receiving(waits_beside_sources("mine")(_wait_rang()))
        return answer

    return run(scheduled(main()))


def test_wait_for_events_beside_sources_drops_another_sources_failure() -> None:
    other = SourceFailed(source="other", error=SourceBroke("other"))
    received = _received_beside_source((other, Rang("a"), Rang("b")))
    assert received == (Rang("a"), Rang("b")), received


def test_wait_for_events_beside_sources_raises_its_own_sources_failure() -> None:
    mine = SourceFailed(source="mine", error=SourceBroke("mine"))
    with pytest.raises(SourceBroke, match="mine"):
        _received_beside_source((Rang("a"), mine))
