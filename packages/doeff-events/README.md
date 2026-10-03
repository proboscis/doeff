# doeff-events

Generic publish/subscribe effects for doeff.

## API

- `Publish(event)`
- `WaitForEvent(*event_types)`
- `event_handler()`
- `subscribed_event_handler(bus, subscriber, event_types=())`・`EventBus()`・`SubscriberQueue(event_types)`

## 購読者ごとの列

`event_handler()` は、その時に待っている全員へ合図を渡し、待ち手が居なければ合図を捨てます。状態を読んでから
`WaitForEvent` に入るまでの間に発した合図を落とさないためには、`subscribed_event_handler()` を使います。購読は handler を
組み立てた時に始まり、待ち手が居ない間に発した合図も購読者ごとの列に積みます。購読者の名前・列・`EventBus` は handler の
組み立ての引数だけに出て、Program(`Publish`・`WaitForEvent`)は変わりません。

| 口 | 振る舞い |
|---|---|
| `subscribed_event_handler(bus, subscriber, event_types=())` | 組み立てた時に `bus` へ購読を始め、その時より後の `event_types` の合図だけを積む。同じ名で組み立て直すと前の列を捨てる。`event_types=()` は発するだけ |
| `Publish(event)` | `bus` の全購読者(自分を含む)の列へ渡す。待っている購読者は起き、待っていない購読者の列には残る |
| `WaitForEvent(*types)` | 自分の列の先頭から当たる合図を取り出す。無ければ合図が来るまで待つ。購読の型の外の型を待つと `ValueError` |
| `SubscriberQueue.offer(event)` | 購読者 1 人の列へ合図を渡す口。別の合図の源を持つ handler も、受けた合図をここへ渡せば同じ待ち方を使える |

待ち手の居ない間に列に溜まった同じ型の合図のうち、欄 `keys`(変わった所の tuple)を持つ frozen の dataclass の合図は、
先に来た方の位置で 1 つにまとめます(`keys` は来た順に重なりを除いて合わせる・`keys` の外の欄が違えばまとめない)。
受け手は所で記録を読み直すので中身は失われません。`keys` を持たない合図(`TimerFired` など)はまとめません。
