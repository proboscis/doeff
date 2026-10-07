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

## process をまたぐ知らせ(notice broker — agora-redesign #3850)

業務の Program は今までどおり `Publish(event)` と `WaitForEvent(*types)` だけを書きます。出来事を別の process へ運ぶのは
`notice_events_handler` で、運び方(channel の名・値の綴り方)はその引数の道の表にだけ書きます。Program には channel の名は
出ません。運ぶのは保存しない知らせ(Redis の Pub/Sub)だけです。

```python
from doeff_events import (
    EventBus, MarkGap, MemoryBroker, NoticeRoute, NoticeSent, SourceMissed, SourceResumed, SourceStarted,
    broker_back_by_retry, memory_notice_handler, notice_events_handler, redis_notice_handler, subscribed_event_handler,
)

routes = (
    NoticeRoute(
        event_type=TurnState,                              # Program が出す・待つ型
        wire_name="turn-state",                            # 線の上の名(同じ channel に複数の型が乗る)
        channel=lambda event: f"turn:{event.conversation}",  # 出す先の channel を出来事の値から決める
        encode=lambda event: json.dumps(...),              # 型 → 文字列
        decode=lambda body: TurnState(...),                # 文字列 → 型
        when_unsent=MarkGap(),                             # 届かなかった時: channel に欠けの印(Drop() = 持たない)
        reads=("turn:c1",),                                # この process が受ける channel(出すだけなら ())
    ),
)

# 外 → 内。時計の handler(GetTime・WaitWithin)は broker に届かない間だけ使います。
program = subscribed_event_handler(EventBus(), "screen", (TurnState, SourceStarted, SourceResumed))(
    redis_notice_handler("redis://agora-events:6379/0", timeout_seconds=2.0)(  # 手元の模擬とテストは memory_notice_handler(MemoryBroker())
        broker_back_by_retry(retry_seconds=2.0)(             # Redis の戻りを、待っている間だけ繋がるかの試しで知る
            notice_events_handler("screen", routes, patience_seconds=900.0)(body)
        )
    )
)
```

- 出す側: 道の表に在る型の `Publish` を broker へ送り(`PUBLISH`)、答えは `NoticeSent(receivers)` — その時に購読していた受け手の数
  です(0 = 誰も聞いていない。0 をどう扱うかは出し手が決めます。1 以上でも、受け手が読んだ事の証ではありません)。表に無い型は外の
  handler(process の中の列)へそのまま出ます。
- 出し損ね(扱いはここ 1 か所 — ADR-DOE-EVENTS-002 R5): broker に届かなくても `Publish` は例外を上げず、閉じた型の 3 つのどれかで答えます —
  `NoticeSent`・`NoticeGapMarked`(道の `when_unsent` が `MarkGap` — その channel に欠けの印を付けた)・`NoticeDropped`(`Drop` — 持たない。
  すぐ古く成る物のため)。持つのは channel の印だけで、出来事そのものは持ちません(持つ量は channel の数で止まる)。欠けは定まった知らせ 1 通
  (`GAP_NOTICE`)で、broker が戻った時(`AwaitBrokerBack` の答え)・次に通る `Publish` の前・出し手の起動の時(`MarkGap.start_channels`)に
  出します。受ける包みはそれを `SourceMissed(source, channel)` にし、Program は `SourceStarted` / `SourceResumed` と同じく記録から 1 度
  追いつきます(何度来てもよい)。別の task が欠けを出している間の `Publish` は、それが終わるのを待ってから出ます(順が入れ替わらない)。
  次の `Publish` が欠けを全部出したら、戻りを待つ task は止まります。`AwaitBrokerBack` に誰も答えられない時、その task は止まり、印は
  次の `Publish` まで残り、その失敗は本体が終わる時に上がります。本体が終われば印は消えます(次の起動の欠けの知らせで埋める)。
- 受ける側: 包んだ本体の最初の effect より前に購読を始め、**broker が購読を確かめた後に** `SourceStarted(source)` を 1 度 出します。
  受けた知らせは全部 process の中の購読者の列へ `Publish` し、Program は `WaitForEvent` で 1 つずつ受けます(`WaitForEvent` に答えるのは
  外の `subscribed_event_handler` — `SourceStarted`・`SourceStalled`・`SourceResumed` は、購読の型に名指した Program だけが受けます)。
  道の表で読めない知らせは捨てずに、源を `UnroutedNotice` で落とします。
- 届かない物: 知らせは、出された時に購読していて繋がっている読み手にだけ届きます。購読の前と、繋がっていない間の知らせは後から
  届きません。失った分は backend が埋めず、Program が `SourceStarted` と `SourceResumed` を受けた時に 1 度 記録から追いつく前提です
  (どちらも購読が成った後にだけ出るので、追いつきの読みの後に出た知らせは落ちません)。
- 接続が切れた時: 源は `SourceStalled` を出し、戻りを `AwaitBrokerBack` の答えで待ちます(上限 `patience_seconds` の `WaitWithin` 1 つ —
  時間で読み直しません)。戻れば購読を作り直し、broker が確かめた後に `SourceResumed` を 1 度 出して続けます。上限を過ぎれば
  `NoticeSourceUnreachable` で落ちます(`SourceFailed` が本体の待ちに届く)。`AwaitBrokerBack` に答えるのは、memory の handler では handler
  自身、Redis では `broker_back_by_retry(retry_seconds)`(`redis_notice_handler` の内側・`notice_events_handler` の外側に置く)です —
  落ちている Redis からは知らせが来ないので、待っている間だけ、組み立てが名指す間隔(既定なし)で「繋がるか」だけを試します
  (`ProbeBroker` = `PING`・data を読み書きしない)。待つ者が居ない間の試しは 0 です(ADR-DOE-EVENTS-002 R6)。
- 読むのが遅い受け手: Redis は、購読者あての未送の知らせが `client-output-buffer-limit pubsub`(既定 32mb 8mb 60)を越えると、その
  購読者の接続を切ります。受け手には「接続が切れた」(`SourceStalled` → `SourceResumed`)として届き、知らせが黙って抜ける事はありません
  (本物の redis-server 7.2.7 のテストで確かめています)。
- 止め方: 源の task は時間の上限の無い blocking の取りで待ち、本体が終わると `Cancel` で止まります(`await_handler` が取り消しを
  redis-py の coroutine へ伝える)。本体の止めは今までどおり `StopArrived` を `WaitForEvent` で受けます。

下の層(broker の操作の effect — `doeff_events.effects.notices`): `Announce`(答え = 受け手の数)・`SubscribeChannels`(答えるのは broker が
確かめた後)・`NextAnnouncement`・`CloseSubscription`・`AwaitBrokerBack`。broker に届かない時は例外でなく `BrokerUnreachable` を答えます。
答える handler は `memory_notice_handler(MemoryBroker())`(process の中・テストは `cut_broker` / `restore_broker` で止まりを作れる)と
`redis_notice_handler(url, timeout_seconds)`(extra `doeff-events[redis]` が要る・設定は URL と、繋ぐ・送るの答えを待つ上限 `timeout_seconds` — 既定なし。答えない server や黙った網が出し手を止めない)。

配達の法は `doeff_events.notice_laws` の筋書き(Program)の 1 か所に在り、memory と本物の redis-server の両方が同じ筋書きを通ります
(宣言 = `docs/adr/defadr_doeff_events_002_notice_delivery_laws.hy`)。本物を相手にするテストは `PATH` の `redis-server` を一時の dir で
起動します。実行 file が無ければ、そのテストだけを訳つきで skip します(memory を相手にするテストは必ず走ります)。

## ファイルの変更を待つ(`WatchFiles` / `NextFileChanges` / `CloseFileWatch`)

file が伸びたかを決まった間隔で読みに行かず、OS の変更の知らせで起きます(#3977)。

| 口 | 振る舞い |
|---|---|
| `WatchFiles(directory)` | 絶対 path の dir(と下)の監視を始める。答えの後の変更は次の `NextFileChanges` が受ける(待っていない間の変更も失わない)。dir が無ければ `WatchRefused` |
| `NextFileChanges(watch)` | 次の変更まで待ち、変わった path(作る・書く・消す)を `FilesChanged(paths)` で答える。時間の上限は無い |
| `CloseFileWatch(watch)` | 監視を止め、handler が持つ物を捨てる |
| `os_file_watch_handler()` | OS の知らせ(Linux の inotify・macOS の FSEvents)を `watchfiles` で受ける。extra `doeff-events[files]` が要る。`await_handler` の内側(`scheduled` の内)に置く |
| `memory_file_watch_handler(files)` | 模擬と検の handler。書き手の役が `announce_file_change(files, path)` を撃つと、その path を持つ監視が起きる |

