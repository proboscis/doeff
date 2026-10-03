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

## 出来事を待つループの macro `event-loop`

係のループを「状態を読む → 出来事か止めの合図を待つ → 来た物の節を回す」の 1 つの形で書く Hy の macro です
(設計 = agora-controllers の `docs/design/event-waits/README.md` 4 節)。

```hy
(require doeff-events.macros [event-loop])

(event-loop [board Board (! (read-board))]    ; state の名・型・初期値(初期値は do! の中 — 読みは (! …) で書く)
  (:stop reason)          board                ; 止めの節(必ず 1 つ)— この値が event-loop の値
  (BoardMoved :keys keys) (! (read-board-at board keys))   ; 本体の値が次の state
  (TimerFired :tag tag)   (match tag "end" (stop board) _ board))   ; (stop 値) で抜ける
```

- state の型を書くと(`[名 型 初期値]`)、初期値・各節の値・抜けた値をその型で確かめ、型検査にもループの値の型が見えます。
  型を省いた `[名 初期値]` も書けます(型検査はループの値を Unknown と見ます)。

- 節の頭の型の組をそのまま `WaitForEvent` に渡すので、待つ型と扱う型はずれません。扱わない型の出来事は節に来ません。
- 止めの合図は `AwaitStop` と競わせます(ループの寿命の間に 1 つだけ待つ)。節の処理中に来た止めは、列に残る次の出来事より先に効きます。
  ループの前に来ていた止めでは、出来事を 1 つも回さずに止めの節を回します。
- 節の本体は `do!` の中です(`<-` と `!` が使える・`(do …)` の中身は文として並ぶ)。state を省く形は `[名 初期値]` を書かずに節だけを並べます。
- 回した出来事ごとに `slog("event-loop", event=<型の名>)` を記録します(外に slog の handler が要ります)。
- `(stop 値)` は本体の最後の値の位置(`do`・`if`・`cond`・`match`・`try` の枝)に書きます。
- 型の節は書いた順に当たります(親の型の節を先に書くと、子の型の出来事も親の節が受けます)。
- 止めの合図で抜ける時、待ちの途中で列から取り出された出来事が 1 つ捨てられることがあります(合図は「どこが変わったか」だけなので、
  次に起きた係が記録を読み直せば揃います)。
- 本体は `do!` の入れ子の関数なので、外の `var` を `:=` で書き換えても外には届きません。次の state は本体の値で渡します。

展開の時に断る形: 止めの節が無い・2 つ在る / 待つ型の節が無い / 型を導けない節(`_`・名前だけ・`|`・値の式)/ 同じ型の節が 2 つ /
型の節の束縛に値の式を書く(値で絞らず本体で分ける)/ 本体の無い節 / 最後の値の位置でない `stop`。
