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

## process をまたぐ出来事(event broker — agora-redesign #3850)

業務の Program は今までどおり `Publish(event)` と `WaitForEvent(*types)` だけを書きます。出来事を別の process へ運ぶのは
`stream_events_handler` で、運び方(channel や列の名・値の綴り方)はその引数の道の表にだけ書きます。Program には channel の名も
列の名も group も確かめ(ack)も出ません。

```python
from doeff_events import (
    EventBus, EventRoute, RouteKind, SourceResumed, SourceStarted,
    memory_stream_handler, MemoryBroker, redis_stream_handler,
    stream_events_handler, subscribed_event_handler,
)

routes = (
    EventRoute(
        event_type=TurnState,                 # Program が出す・待つ型
        wire_name="turn-state",               # 線の上の名(同じ channel に複数の型が乗る)
        kind=RouteKind.NOTICE,                # 知らせ(Pub/Sub)。確かめる列は RouteKind.ACKED_STREAM
        place=lambda event: f"turn:{event.conversation}",   # 出す先の名を出来事の値から決める
        encode=lambda event: json.dumps(...),               # 型 → 文字列
        decode=lambda position, body: TurnState(...),       # (列の位置, 文字列) → 型。知らせの位置は ""(NO_POSITION)
        reads=("turn:c1",),                   # この process が受ける channel(出すだけなら ())
    ),
)

# 外 → 内。時計の handler(GetTime・WaitWithin)は broker に届かない間だけ使います。
program = subscribed_event_handler(EventBus(), "screen", (TurnState, SourceStarted, SourceResumed))(
    redis_stream_handler("redis://agora-events:6379/0")(      # 手元の模擬とテストは memory_stream_handler(MemoryBroker())
        stream_events_handler("screen", routes, patience_seconds=900.0)(body)
    )
)
```

- 出す側: 道の表に在る型の `Publish` を broker へ送ります(知らせ = `PUBLISH`・確かめる列 = `XADD`)。broker に届かなければ、その
  `Publish` は Program の中で `EventNotPublished` を上げます。表に無い型は外の handler(process の中の列)へそのまま出ます。
- 受ける側: 包んだ本体の最初の effect より前に購読を始め、初めて繋がった時に `SourceStarted(source)` を 1 度 出します。受けた出来事は
  process の中の購読者の列へ `Publish` し、Program は `WaitForEvent` で 1 つずつ受けます(`WaitForEvent` に答えるのは外の
  `subscribed_event_handler` — `SourceStarted`・`SourceStalled`・`SourceResumed`・`SourceGap` は、購読の型に名指した Program だけが受けます)。
- 知らせは、出された時に購読していて繋がっている読み手にだけ届きます。購読の前と、繋がっていない間の知らせは後から届きません。
  失った分は、Program が `SourceStarted` と `SourceResumed` を受けた時に 1 度 記録から追いつく前提です。
- broker に届かない間: 源は `SourceStalled` を出し、戻りを `AwaitBrokerBack` の答えで待ちます(上限 `patience_seconds` の `WaitWithin`
  1 つ — 時間で読み直しません)。戻れば `SourceResumed` を 1 度 出して続け、上限を過ぎれば `StreamSourceUnreachable` で落ちます
  (`SourceFailed` が本体の待ちに届く)。`AwaitBrokerBack` に答えるのは、memory の handler では handler 自身、Redis の handler では
  組み立ての側(broker の Service の戻りを知る物)です — Redis の handler は答えません。
- 止め方: 源の task は時間の上限の無い blocking の取りで待ち、本体が終わると `Cancel` で止まります(`await_handler` が取り消しを
  redis-py の coroutine へ伝える)。本体の止めは今までどおり `StopArrived` を `WaitForEvent` で受けます。

確かめる列(`RouteKind.ACKED_STREAM` — Redis Streams と consumer group)も同じ工場で使えます: group の名は工場の引数 `consumer`
(読み手ごとに 1 つ)、確かめは暗黙(Program が次の `WaitForEvent` へ戻った時か例外なく終わった時に、前に渡した 1 つを確かめる)、
起動と繋ぎ直しの時に未確かめを引き取り、列の頭が長さの上限(`EventRoute.maxlen`)で切られていたら `SourceGap(source)` を出します。
範囲を 1 度 読む口は `EventsBetween(型, after, until)`(答え = `EventsRead` か `RangeCut`)。

下の層(broker の操作の effect — `doeff_events.effects.streams`): `Announce`・`SubscribeChannels`・`NextAnnouncement`・`AppendEntry`・
`EnsureGroup`・`ReadGroup`・`AckEntry`・`ClaimPending`・`ReadGroupPosition`・`ReadEntryRange`・`AwaitBrokerBack`。どれも broker に
届かない時は例外でなく `BrokerUnreachable` を答えます。答える handler は `memory_stream_handler(MemoryBroker())`(process の中・
テストは `cut_broker` / `restore_broker` で止まりを作れる)と `redis_stream_handler(url)`(extra `doeff-events[redis]` が要る・
設定は URL だけ)。

配達の法は `doeff_events.stream_laws` の筋書き(Program)の 1 か所に在り、memory と本物の redis-server の両方が同じ筋書きを通ります
(宣言 = `docs/adr/defadr_doeff_events_002_broker_delivery_laws.hy`)。本物を相手にするテストは、環境変数 `DOEFF_REDIS_SERVER`
(実行 file の path)か `PATH` の `redis-server` を一時の dir で起動します。実行 file が無ければ、そのテストだけを訳つきで skip します。
