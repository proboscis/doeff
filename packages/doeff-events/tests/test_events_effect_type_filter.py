"""出来事の handler の effect の引数の型の註の検(agora-redesign #2670 の根 A)。

VM は handler の effect の引数の型の註を install の時に読み(doeff_vm._effect_types・SPEC-WITHHANDLER-TYPE-FILTER)、型の外の effect では
その handler を Python に入らずに外へ渡す。memory の 2 つの handler(event_handler・subscribed_event_handler)は Publish と WaitForEvent
にだけ答え、ほかは Pass で外へ渡すだけなので、註で 2 つの型に絞っても答えと順は変わらず、本体に入る回数だけが減る(註が EffectBase の
間は、係の本体の全部の effect がこの handler を Pass で通り、agora の手番の筋書き 1 本で 7,000〜11,000 歩を使っていた)。
"""

import sys
from collections.abc import Callable
from dataclasses import dataclass
from types import CodeType, FrameType

from doeff_core_effects import Ask
from doeff_core_effects.handlers import reader
from doeff_events import EventBus, Publish, event_handler, subscribed_event_handler
from doeff_events.effects import PublishEffect, WaitForEventEffect
from doeff_vm._effect_types import handler_effect_types
from events_test_support import run_scheduled

from doeff import do


@dataclass(frozen=True)
class Changed:
    """検の中だけの合図。"""

    key: str


def _raw(installed: object) -> Callable[..., object]:
    """Program -> Program の handler の値が包む、VM が呼ぶ handler の関数(doeff.program.handler が置く欄)。"""
    return installed.__doeff_handler_data__  # type: ignore[attr-defined] — doeff.program.handler が置く欄(型の無い欄)


@dataclass(frozen=True)
class Entered:
    """run の間に code の関数が始まった回数(entered)と、run の答え(result)。"""

    entered: int
    result: object


def _entries(code: CodeType, run: Callable[[], object]) -> Entered:
    """run の間に code の関数が始まった回数と、run の答え。本体は generator なので再開ごとにも call の事象が出る — frame の同一性で数える。"""
    frames: dict[int, FrameType] = {}

    def profile(frame: FrameType, event: str, _arg: object) -> None:
        if event == "call" and frame.f_code is code:
            frames.setdefault(id(frame), frame)

    sys.setprofile(profile)
    try:
        result = run()
    finally:
        sys.setprofile(None)
    return Entered(entered=len(frames), result=result)


@do
def _asks_then_publish():
    a = yield Ask("a")
    b = yield Ask("b")
    c = yield Ask("c")
    yield Publish(Changed("x"))
    return a + b + c


def _subscriber_code() -> CodeType:
    """subscribed_event_handler が組み立てる handler の関数の code(閉包ごとに別の関数でも code は 1 つ)。"""
    return _raw(subscribed_event_handler(EventBus(), "code", (Changed,))).__wrapped__.__code__  # type: ignore[attr-defined] — @do の functools.wraps が置く欄


def test_both_memory_handlers_declare_the_effects_they_answer() -> None:
    assert handler_effect_types(_raw(subscribed_event_handler(EventBus(), "s", (Changed,)))) == (WaitForEventEffect, PublishEffect)
    assert handler_effect_types(_raw(event_handler())) == (WaitForEventEffect, PublishEffect)


def test_an_effect_outside_the_types_does_not_enter_the_subscriber() -> None:
    # 購読者の handler は内側: Ask は購読者の handler を飛ばして外の reader に届き、本体に入るのは Publish の 1 回だけ
    seen = _entries(
        _subscriber_code(),
        lambda: run_scheduled(
            reader({"a": 1, "b": 2, "c": 3})(subscribed_event_handler(EventBus(), "s", (Changed,))(_asks_then_publish()))
        ),
    )
    assert seen.result == 6
    assert seen.entered == 1, "購読者の handler の本体に入るのは Publish の 1 回だけ(Ask 3 回は飛ばす)"
