"""In-memory publish/subscribe handlers.

``event_handler()`` は、その時に待っている全員へ合図を同報し、待ち手が居なければ合図を捨てる(元の形のまま)。
``subscribed_event_handler()`` は購読者ごとの列を持ち、待ち手が居ない間に発した合図も列に積む — 状態を読んでから
``WaitForEvent`` に入るまでの間に別の task が発した合図を落とさない(agora-redesign #3075・設計 #3072)。
"""

import inspect
from typing import TYPE_CHECKING, Final, final

from doeff_core_effects.scheduler import CompletePromise, CreatePromise, Promise, Wait

from doeff import EffectBase, K, Pass, Resume, ResumeThrow, do
from doeff import handler as _program_handler
from doeff_events.effects import PublishEffect, WaitForEventEffect

if TYPE_CHECKING:
    from doeff import EffectGenerator
    from doeff.program import ProgramHandler


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
    Publish resolves promises for listeners whose registered type matches.
    """
    listeners: dict[type, list] = {}

    @do
    def handler(effect, k):
        if isinstance(effect, WaitForEventEffect):
            promise = yield CreatePromise()
            for event_type in effect.event_types:
                listeners.setdefault(event_type, []).append(promise)
            try:
                event = yield Wait(promise.future)
            finally:
                _remove_promises(listeners, {id(promise)})
            result = yield Resume(k, event)
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


@final
class SubscriberQueue:
    """購読者 1 人の合図の列。

    ``offer(event)`` は、購読の型に当たる合図を、型の合う待ち手が居ればその約束へ(来た順に 1 人)、居なければ
    列の末尾へ渡す。購読の型に当たらない合図は捨てる。``take(types)`` は列の先頭から ``types`` に当たる最初の合図を
    取り出す。1 つの合図は購読者ごとに 1 度だけ渡る(待ち手の 1 人か、列の 1 か所)。

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

        型の合う待ち手が居なければ列の末尾に積んで ``None``。購読の型に当たらなければ捨てて ``None``。
        """
        if not isinstance(event, self._event_types):
            return None
        for waiter in self._waiters:
            if isinstance(event, waiter.event_types):
                self._waiters.remove(waiter)
                return waiter.promise
        self._pending.append(event)
        return None

    def take(self, wanted: tuple[type, ...]) -> object | Empty:
        """列の先頭から ``wanted`` に当たる最初の合図を取り出す。無ければ ``EMPTY``。"""
        for index, event in enumerate(self._pending):
            if isinstance(event, wanted):
                del self._pending[index]
                return event
        return EMPTY

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
    同じ ``subscriber`` の名で組み立て直すと、前の列を捨てて新しく始める(落ちた後の再起動 — 追いつくのは Program が
    最初に記録を読むこと)。``event_types=()`` は発するだけの handler。

    - ``Publish(event)``: ``bus`` の全購読者(自分を含む)の列へ渡し、起きる待ち手の約束を完了してから続ける。発した
      合図は、その時に待っていない購読者の列にも残る。
    - ``WaitForEvent(*types)``: 自分の列から取り出す。無ければ内側の約束(``CreatePromise``・``Wait``)で待つ。
      ``types`` に購読の型の外の型が在れば ``ValueError``(満たされない待ち = 配線の誤りを、その場で名指す)。

    購読者の名前・列・``bus`` はこの組み立ての引数だけに出て、Program には出ない。ack も cursor も無い。
    """
    queue = bus.subscribe(subscriber, event_types)

    @do
    def handler(effect: EffectBase, k: K) -> "EffectGenerator[object]":
        """この購読者の Program の Publish・WaitForEvent に、bus と自分の列で答える。ほかの effect は外へ渡す。"""
        match effect:
            case WaitForEventEffect(event_types=wanted):
                outside = queue.outside(wanted)
                if outside:
                    names = ", ".join(t.__name__ for t in outside)
                    subscribed = ", ".join(t.__name__ for t in queue.event_types) or "なし"
                    return (
                        yield ResumeThrow(
                            k,
                            ValueError(
                                f"購読者 {subscriber!r} は購読の型の外を待った: {names}"
                                f"(購読の型: {subscribed})— 外の型の合図は列に積まれず、待ちが満たされない"
                            ),
                        )
                    )
                found = queue.take(wanted)
                if isinstance(found, Empty):
                    promise: Promise[object] = yield CreatePromise()
                    queue.add_waiter(wanted, promise)
                    try:
                        found = yield Wait(promise.future)
                    finally:
                        queue.remove_waiter(promise)
                return (yield Resume(k, found))
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
