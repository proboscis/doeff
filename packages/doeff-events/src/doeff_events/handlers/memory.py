"""In-memory publish/subscribe handlers.

``event_handler()`` は、その時に待っている全員へ合図を同報し、待ち手が居なければ合図を捨てる(元の形のまま)。
``subscribed_event_handler()`` は購読者ごとの列を持ち、待ち手が居ない間に発した合図も列に積む — 状態を読んでから
``WaitForEvent`` に入るまでの間に別の task が発した合図を落とさない(agora-redesign #3075・設計 #3072)。列に溜まった
同じ型の「所が変わった」合図は 1 つにまとめる(agora-redesign #3079)。``WaitForEvents`` は 1 つ以上届くまで待ち、その時に列に在る
当たる合図を全部、来た順の組で受ける — 同じ刻に届いた複数の知らせを 1 回の読みにまとめる。
"""

import inspect
from dataclasses import fields, is_dataclass, replace
from typing import TYPE_CHECKING, Final, Protocol, TypeGuard, final

from doeff_core_effects.scheduler import CompletePromise, CreatePromise, Promise, Wait

from doeff import K, Pass, Resume, ResumeThrow, do
from doeff import handler as _program_handler
from doeff_events.effects import (
    PublishEffect,
    SourceFailed,
    StopArrived,
    WaitForEventEffect,
    WaitForEventsEffect,
)

if TYPE_CHECKING:
    from collections.abc import Hashable

    from _typeshed import DataclassInstance

    from doeff import EffectGenerator
    from doeff.program import ProgramHandler

    class _KeyedSignal(DataclassInstance, Protocol):
        """まとめてよい合図の形: 欄 ``keys`` に変わった所の tuple を持つ frozen の dataclass。"""

        @property
        def keys(self) -> tuple[Hashable, ...]:
            """変わった所(doeff-records の記録の合図の源では ``ChangedRow``)。"""
            ...


def _matching_promises(listeners, event):
    matched = {}
    for event_type, queued in listeners.items():
        if not isinstance(event, event_type):
            continue
        for promise in queued:
            matched.setdefault(id(promise), promise)
    return matched


def _remove_promises(listeners, promise_ids):
    if not promise_ids:
        return
    for event_type, queued in list(listeners.items()):
        remaining = [p for p in queued if id(p) not in promise_ids]
        if remaining:
            listeners[event_type] = remaining
        else:
            listeners.pop(event_type, None)


def event_handler():
    """Create a stateful in-memory pub/sub handler.

    WaitForEvent creates a Promise and blocks via Wait(promise.future).
    WaitForEvents waits the same way and answers the one event it received as ``(event,)`` (there is no queue to
    hold more).
    Publish resolves promises for listeners whose registered type matches.
    """
    listeners: dict[type, list] = {}

    @do
    def handler(effect: WaitForEventEffect | WaitForEventsEffect | PublishEffect, k):
        if isinstance(effect, WaitForEventEffect | WaitForEventsEffect):
            promise = yield CreatePromise()
            for event_type in effect.event_types:
                listeners.setdefault(event_type, []).append(promise)
            try:
                event = yield Wait(promise.future)
            finally:
                _remove_promises(listeners, {id(promise)})
            answer = (event,) if isinstance(effect, WaitForEventsEffect) else event
            result = yield Resume(k, answer)
            return result

        if isinstance(effect, PublishEffect):
            event = effect.event
            matched = _matching_promises(listeners, event)
            _remove_promises(listeners, set(matched))
            for promise in matched.values():
                yield CompletePromise(promise, event)
            result = yield Resume(k, None)
            return result

        yield Pass(effect, k)

    return _program_handler(handler)


@final
class Empty:
    """``SubscriberQueue.take`` の印: 列に、求めた型に当たる合図が無い。

    合図そのものが ``None`` でもよいので、「無い」は合図と混ざらない別の値で表す。比べは ``isinstance(x, Empty)``
    か ``x is EMPTY``。
    """

    __slots__ = ()

    def __repr__(self) -> str:
        """検の失敗の文や log で、印を合図と見分けられる名で出す。"""
        return "EMPTY"


EMPTY: Final[Empty] = Empty()


@final
class _Waiter:
    """列の待ち手 1 人: ``WaitForEvent`` が求めた型の組と、合図を渡す約束の対。比べは同一性(約束 1 つに待ち手 1 人)。"""

    __slots__ = ("event_types", "promise")

    def __init__(self, event_types: tuple[type, ...], promise: Promise[object]) -> None:
        """待ち手を作る — ``offer`` が、合図の型と ``event_types`` を照らしてこの約束を返すため。"""
        self.event_types: Final = event_types
        self.promise: Final = promise


def _require_types(event_types: tuple[type, ...]) -> tuple[type, ...]:
    # 型の注記の無い呼び手(Hy・素の Python)からの入力を、組み立てた時に確かめる(合図を渡す時まで遅らせない)。
    for event_type in event_types:
        if not inspect.isclass(event_type):
            raise TypeError(
                f"SubscriberQueue event types must be type objects, got {event_type!r}"
            )
    return event_types


_KEYS: Final = "keys"


def _coalescible(event: object) -> "TypeGuard[_KeyedSignal]":
    """``event`` を列の中で同じ型の合図とまとめてよいか — まとめる規則の判定はこの 1 か所。

    まとめてよい = frozen の dataclass で、組み立ての欄 ``keys`` に tuple を持つ合図(doeff-records の記録の合図の源が
    出す形 — 要素は hash できる所の値で、まとめる時に重なりを除くのに使う)。この形の合図は「所 ``keys`` が変わった」
    だけを運び、受け手は所で記録を読み直すので、まだ渡していない同じ型の合図と ``keys`` を合わせて 1 つにしても受け手の
    作る状態は変わらない。``keys`` を持たない合図(``TimerFired`` など — tag ごとに意味が違う)はまとめない。
    """
    if isinstance(event, type) or not is_dataclass(event):
        return False
    params: object = getattr(type(event), "__dataclass_params__", None)
    if getattr(params, "frozen", False) is not True:
        return False
    if not any(field.name == _KEYS and field.init for field in fields(event)):
        return False
    keys: object = getattr(event, _KEYS)
    return isinstance(keys, tuple)


def _same_apart_from_keys(queued: "_KeyedSignal", arriving: "_KeyedSignal") -> bool:
    """2 つの合図が同じ型で、``keys`` の外の欄が等しいか — 違う欄が在れば、まとめると片方の欄が消えるのでまとめない。"""
    if type(queued) is not type(arriving):
        return False
    return all(
        getattr(queued, field.name) == getattr(arriving, field.name)
        for field in fields(queued)
        if field.name != _KEYS
    )


def _with_keys_of(queued: "_KeyedSignal", arriving: "_KeyedSignal") -> "_KeyedSignal":
    """``queued`` に ``arriving`` の ``keys`` を合わせた合図(来た順・重なりは最初の 1 つだけ残す)。"""
    # dict.fromkeys は来た順を保って重なりを除く(値は使わない)。
    return replace(queued, keys=tuple(dict.fromkeys((*queued.keys, *arriving.keys))))


@final
class SubscriberQueue:
    """購読者 1 人の合図の列。

    ``offer(event)`` は、購読の型に当たる合図を、型の合う待ち手が居ればその約束へ(来た順に 1 人)、居なければ
    列へ渡す。列に同じ型のまとめてよい合図(``_coalescible``)が既に在れば、その合図を ``keys`` を合わせた 1 つに
    置き換え(先に来た方の位置のまま)、無ければ末尾に積む。購読の型に当たらない合図は捨てる。``take(types)`` は列の
    先頭から ``types`` に当たる最初の合図を取り出す。1 つの合図は購読者ごとに 1 度だけ渡る(待ち手の 1 人か、列の
    1 か所 — まとめた合図はその ``keys`` の中)。``take_all(types)`` は ``types`` に当たる合図を全部、来た順に取り出す。

    記録の service から合図を受ける handler(agora-redesign #3077)も、受けた合図をこの ``offer`` に渡せば、
    ``WaitForEvent`` の側の待ち方(``take`` → 無ければ待ち手として約束で待つ)を共有できる。
    """

    __slots__ = ("_event_types", "_pending", "_waiters")

    def __init__(self, event_types: tuple[type, ...]) -> None:
        """空の列を作る — 作った時より後に ``offer`` された合図だけが積まれる(購読の始まり)。"""
        self._event_types: Final = _require_types(tuple(event_types))
        self._pending: Final[list[object]] = []
        self._waiters: Final[list[_Waiter]] = []

    @property
    def event_types(self) -> tuple[type, ...]:
        """購読の型(``()`` = 何も積まない — 発するだけの購読者)。"""
        return self._event_types

    def outside(self, wanted: tuple[type, ...]) -> tuple[type, ...]:
        """``wanted`` のうち購読の型の外の型。

        型 t が内 = 購読の型のどれかの部分型(t に当たる合図は必ず列に入る)。外の型を待つと、その型の合図の一部か
        全部が列に入らず、待ちが満たされない。
        """
        return tuple(t for t in wanted if not issubclass(t, self._event_types))

    def offer(self, event: object) -> Promise[object] | None:
        """``event`` を列へ渡す。起こす待ち手の約束を返す(待ち手からは外す・呼び手が ``CompletePromise`` する)。

        型の合う待ち手が居なければ列に置いて ``None`` — 同じ型のまとめてよい合図が列に在ればそれと 1 つにまとめ、
        無ければ末尾に積む。購読の型に当たらなければ捨てて ``None``。
        """
        if not isinstance(event, self._event_types):
            return None
        for waiter in self._waiters:
            if isinstance(event, waiter.event_types):
                self._waiters.remove(waiter)
                return waiter.promise
        self._place(event)
        return None

    def _place(self, event: object) -> None:
        """待ち手の居ない ``event`` を列に置く: 同じ型のまとめてよい合図が在ればその位置でまとめ、無ければ末尾に積む。"""
        if _coalescible(event):
            for index, queued in enumerate(self._pending):
                if _coalescible(queued) and _same_apart_from_keys(queued, event):
                    self._pending[index] = _with_keys_of(queued, event)
                    return
        self._pending.append(event)

    def take(self, wanted: tuple[type, ...]) -> object | Empty:
        """列の先頭から ``wanted`` に当たる最初の合図を取り出す。無ければ ``EMPTY``。"""
        for index, event in enumerate(self._pending):
            if isinstance(event, wanted):
                del self._pending[index]
                return event
        return EMPTY

    def take_all(self, wanted: tuple[type, ...]) -> tuple[object, ...]:
        """列の先頭から ``wanted`` に当たる合図を全部取り出し、来た順の組で返す。無ければ空の組。当たらない合図は列に残る。"""
        taken = tuple(event for event in self._pending if isinstance(event, wanted))
        self._pending[:] = [event for event in self._pending if not isinstance(event, wanted)]
        return taken

    def add_waiter(self, wanted: tuple[type, ...], promise: Promise[object]) -> None:
        """``wanted`` の合図を ``promise`` で待つ待ち手を末尾に足す(起こすのは来た順)。"""
        self._waiters.append(_Waiter(_require_types(tuple(wanted)), promise))

    def remove_waiter(self, promise: Promise[object]) -> None:
        """``promise`` の待ち手を外す。合図を渡し済みで既に居なければ何もしない。"""
        self._waiters[:] = [waiter for waiter in self._waiters if waiter.promise is not promise]


@final
class EventBus:
    """memory の合図の置き場: 購読者の名前 → その購読者の列。

    1 つの process(模擬では 1 つの世界)に 1 つ置き、同じ bus を渡した ``subscribed_event_handler`` どうしが合図を
    交わす。Program(``Publish``・``WaitForEvent``)は bus を見ない — 触るのは handler だけ。
    """

    __slots__ = ("_queues",)

    def __init__(self) -> None:
        """購読者の居ない置き場を作る — 1 つの世界の handler どうしが合図を交わす場として共有する。"""
        self._queues: Final[dict[str, SubscriberQueue]] = {}

    def subscribe(self, subscriber: str, event_types: tuple[type, ...]) -> SubscriberQueue:
        """``subscriber`` の列を新しく始めて返す。同じ名の前の列は捨てる(その時より後の合図だけが積まれる)。"""
        queue = SubscriberQueue(event_types)
        self._queues[subscriber] = queue
        return queue

    def offer(self, event: object) -> tuple[Promise[object], ...]:
        """全購読者の列へ ``event`` を渡し、起こす待ち手の約束を返す(呼び手が ``CompletePromise`` する)。"""
        offered = tuple(queue.offer(event) for queue in self._queues.values())
        return tuple(promise for promise in offered if promise is not None)


def subscribed_event_handler(
    bus: EventBus,
    subscriber: str,
    event_types: tuple[type, ...] = (),
) -> "ProgramHandler":
    """購読者ごとの列を持つ memory の pub/sub handler を組み立てる。

    組み立てた時に ``bus`` へ ``subscriber`` の購読を始め、その時より後に発した ``event_types`` の合図だけを積む。
    待つ型を持つ購読者は、止めの合図 ``StopArrived`` と源の失敗の合図 ``SourceFailed`` も必ず購読する — ループの止めの見張り
    (``event_loop.begin_watch``)と合図の源の task(doeff-records の記録の合図の源)が同じ bus に発し、待つ側はそれを出来事と同じ
    ``WaitForEvent`` で待つ(待つ型の宣言に足し忘れても、止まらない係・源の失敗に気づかない係にならない)。
    同じ ``subscriber`` の名で組み立て直すと、前の列を捨てて新しく始める(落ちた後の再起動 — 追いつくのは Program が
    最初に記録を読むこと)。``event_types=()`` は発するだけの handler(止めと源の失敗の合図も積まない)。

    - ``Publish(event)``: ``bus`` の全購読者(自分を含む)の列へ渡し、起きる待ち手の約束を完了してから続ける。発した
      合図は、その時に待っていない購読者の列にも残る。
    - ``WaitForEvent(*types)``: 自分の列から取り出す。無ければ内側の約束(``CreatePromise``・``Wait``)で待つ。
      ``types`` に購読の型の外の型が在れば ``ValueError``(満たされない待ち = 配線の誤りを、その場で名指す)。
    - ``WaitForEvents(*types)``: 自分の列に当たる合図が在れば全部を来た順の組で答える。無ければ ``WaitForEvent`` と同じく
      約束で 1 つ待ち、起きた後にその時に列に在る残りも足して答える(空の組は答えない)。型の外の検めも同じ。

    購読者の名前・列・``bus`` はこの組み立ての引数だけに出て、Program には出ない。ack も cursor も無い。
    """
    queue = bus.subscribe(subscriber, (*event_types, StopArrived, SourceFailed) if event_types else ())

    def outside_error(wanted: tuple[type, ...]) -> ValueError | None:
        """``wanted`` に購読の型の外の型が在れば、それを名指す ``ValueError``。無ければ ``None``(2 つの待ちの共有の検め)。"""
        outside = queue.outside(wanted)
        if not outside:
            return None
        names = ", ".join(t.__name__ for t in outside)
        subscribed = ", ".join(t.__name__ for t in queue.event_types) or "なし"
        return ValueError(
            f"購読者 {subscriber!r} は購読の型の外を待った: {names}"
            f"(購読の型: {subscribed})— 外の型の合図は列に積まれず、待ちが満たされない"
        )

    @do
    def handler(
        effect: WaitForEventEffect | WaitForEventsEffect | PublishEffect, k: K
    ) -> "EffectGenerator[object]":
        """この購読者の Program の Publish・WaitForEvent・WaitForEvents に、bus と自分の列で答える。effect の型の注記により、ほかの
        effect では VM がこの handler を飛ばす(doeff-vm の _effect_types.py — 本体の全部の effect がここを Pass で通る歩を出さない)。"""
        match effect:
            case WaitForEventEffect(event_types=wanted) | WaitForEventsEffect(event_types=wanted):
                # 2 つの待ちは同じ道を通る(列から 1 つ取る・無ければ約束で 1 つ待つ)。道を下請けの Program に切り出さない — 下請けは待ちの
                # たびに 1 段多く回り、起こされる待ち 1 回が 3 歩増える(tests/test_wait_steps.py)。違いは答えの形だけ。
                error = outside_error(wanted)
                if error is not None:
                    return (yield ResumeThrow(k, error))
                found = queue.take(wanted)
                if isinstance(found, Empty):
                    promise: Promise[object] = yield CreatePromise()
                    queue.add_waiter(wanted, promise)
                    try:
                        found = yield Wait(promise.future)
                    finally:
                        queue.remove_waiter(promise)
                # WaitForEvents は取った 1 つに、その時に列に在る残りを来た順に足して答える(取った 1 つが最初)。
                many = isinstance(effect, WaitForEventsEffect)
                return (yield Resume(k, (found, *queue.take_all(wanted)) if many else found))
            case PublishEffect(event=event):
                for promise in bus.offer(event):
                    yield CompletePromise(promise, event)
                return (yield Resume(k, None))
            case _:
                yield Pass(effect, k)

    return _program_handler(handler)


__all__ = [
    "EMPTY",
    "Empty",
    "EventBus",
    "SubscriberQueue",
    "event_handler",
    "subscribed_event_handler",
]
