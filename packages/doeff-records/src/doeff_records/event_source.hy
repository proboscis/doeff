;;; 記録の変更を出来事の合図として発する源(#3077・設計 #3072)— 記録の変化の待ち(WatchChanges・WatchEvents)で受けた変化を、
;;; 合図の型ごとにまとめて Publish する。WaitForEvent には答えない — 待ちに答えるのは doeff-events の subscribed_event_handler の
;;; 購読者の列 1 つだけ(期限の TimerFired と記録の合図を、同じ WaitForEvent の 1 回で待てる)。
;;;
;;; 合図は「どこが変わったか」だけを運ぶ(状態の差分は運ばない — 受けた Program が記録を読み直す)。使い手は合図の型を dataclass で
;;; 宣言し、欄 keys(ChangedRow の tuple)を持たせる。どの合図の型をどの表・列の変化で起こすかは、組み立ての引数 bindings
;;; (SignalTables の tuple)の 1 か所に置き、Program には位置(cursor)・表と列の名前・購読者の名前を出さない。
;;;
;;; 組み立ては 2 つの形で、どちらも本体を包む関数(with-handlers の列に置ける)を返し、包み方は 1 つ(run-with-sources)を共有する:
;;;   records-signal-handler = 素の工場の関数(Program ではない — 使い手が with-handlers の列に呼びの字面で置き、閉じの検の道具
;;;     doeff-effect-analyzer が工場の宣言 __doeff_handles__ = ()・__doeff_effects__ を読める形)。購読の始まりの位置は、包んだ本体を
;;;     走らせる頭で読む(#3104)。
;;;   records-signal-source = Program(使い手の付け替えまで 1 版残す)。位置を組み立ての時に読み、読んだ位置で同じ包み方を使う。
;;; 購読の始まりの位置 = 結んだ表を ListRows で 1 頁(1 行)読んだ頁の epoch と sequence と、結んだ列の末尾(ReadStreamEnd・空なら 0)。
;;; 位置を読んでから源の task(表の分 1 つ = WatchChanges の long-poll・列 1 つにつき 1 つ = WatchEvents の long-poll)を Spawn し、その後に
;;; 本体を走らせるので、本体の最初の読みより前に購読が始まる(読みと待ちの間の書きを落とさない)。本体は包みを撃った task のまま走らせる
;;; (Spawn しない — 外の task の Cancel がそのまま本体に届き、外の Spawn の優先のまま走る)。task にするのは源だけで、本体が終われば
;;; (答えでも例外でも)源の task を止める。源の失敗は本体の待ちに届ける: 本体の WaitForEvent を子の task で外の購読者の列へ出し、源の
;;; task と Race する(waits-beside-sources)— 源が先に落ちれば本体は同じ例外で落ちる。今の task を引く effect が scheduler に無いので、
;;; 源の側から本体の task を Cancel する形は採らない。WaitForEvent には答えない(答えは外の購読者の列から来る — 子に出し直すだけ)。
;;; 源は記録の置き場への接続を持たず、読みの effect を外へ出す — 外の記録の handler(memory・PostgreSQL・HTTP の口のどれでも)が答えるので、
;;; 手元の模擬と本番の違いは外の記録の handler の接続先だけになる。
;;;
;;; 組む順(外 → 内): subscribed_event_handler(購読者の列)→(timer_handler)→ この源 → 業務の Program。源の task の Publish が購読者の
;;; 列に届くよう、源は subscribed_event_handler の内側に置く(Spawn した task は Spawn した所の handler の下で走る)。
;;;   表の変化 = 1 回の Changes の束を、合図の型ごとに 1 つの合図にまとめる(keys = 結んだ表の変わった行・同じ行は 1 つ)。
;;;              Reset = 位置を WatchCursor(epoch floor) に戻して読み直す(残っている変更をもう一度合図にする — 合図は冪等なので重なってよい)。
;;;   列の追記 = 列の頭が進んだら(EventsMoved)末尾を ReadStreamEnd で読み、その列に結んだ型ごとに合図を 1 つ発する(keys = 列の名前と
;;;              末尾の番号の 10 進の綴り — 受け手は自分の位置から ReadEvents で読み直す)。EventsQuiet なら待ち直すだけ。
;;;   Unreachable = 源の task の中で繋ぎ直す(RECONNECT-TRIES 回まで、RECONNECT-SECONDS 秒ずつ間を置いて撃ち直す)。直らなければ
;;;                 SignalSourceUnreachable で process を落とす(外の再起動が拾う)。業務の Program には Unreachable を見せない。
;;; 待ちの上限と繋ぎ直しの間は、記録の service との境界にあるこの源の中だけの待ち(業務の Program に時間の待ちを出さない)。
;;; 1 つの購読者の列(bus)に源を 1 つ置く形が前提 — 同じ bus に同じ表を見る源を 2 つ置くと、同じ変化の合図が 2 度届く(冪等なので壊れない)。
(require doeff-hy.macros [defhandler defk deff <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "records" :role "foundation"})
(import collections.abc [Callable])
(import dataclasses [dataclass fields is-dataclass])
(import functools [partial])
(import doeff [EffectBase Program with-handlers])
(import doeff_core_effects.scheduler [Cancel Race Spawn Task TaskCancelledError Wait])
(import doeff_events.effects [Publish PublishEffect WaitForEventEffect])
(import doeff_time [Delay DelayEffect])
(import doeff_records.admission [key-text])
(import doeff_records.effects [ListRows ReadStreamEnd WatchChanges WatchEvents])
(import doeff_records.values [Changes EventsMoved EventsQuiet NotIndexed Page Reset StreamEmpty StreamEnd Unreachable WatchCursor
                              checked-table-name])

;; 記録の変化の待ちの上限(秒)— 変化が無ければこの秒で静かな答えが返り、同じ位置から待ち直す(long-poll の 1 回の長さ)。
(val WATCH-SECONDS 30.0)
;; 置き場が Unreachable を返した時に撃つ回数(最初の 1 回を含む)と、撃ち直しの間(秒)。設定の口にはしない。
(val RECONNECT-TRIES 5)
(val RECONNECT-SECONDS 2.0)


(defrecord ChangedRow
  "合図が運ぶ「変わった所」1 つ: table = 表か列の名前 / key = 表の行なら行の鍵の綴り(admission の key-text — 鍵の欄の順の文字列を
   JSON の配列に綴った文字列。tuple の鍵へは admission の key-from-text で戻す)、列なら頭の番号の 10 進の綴り。受けた Program はここを
   読み直す(表 = その鍵の行・列 = 自分の位置から ReadEvents)。"
  {:tags {:context "records" :role "type"}
   :check [(checked-table-name table "ChangedRow.table")
           (and (isinstance key str) key)]}
  (#^ str table)
  (#^ str key))


(defrecord SignalTables
  "合図の型 1 つと、その型の合図を起こす表と列の名前の組の対: signal = 使い手が宣言する合図の型(欄 keys を持つ dataclass — 源が
   (signal :keys 変わった所の tuple) で作る)/ tables = 行の変化でこの型の合図を起こす表の名前 / streams = 追記でこの型の合図を起こす
   列の名前(どちらも既定は空・どちらか 1 つ以上)。欄 keys を持たない型・表も列も無い対は作る時に止める。"
  {:tags {:context "records" :role "type"}
   :check [(isinstance signal type)
           (and (is-dataclass signal) (in "keys" (gfor declared (fields signal) declared.name)))
           (and (isinstance tables tuple) (all (gfor name tables (checked-table-name name "SignalTables.tables の表"))))
           (and (isinstance streams tuple) (all (gfor name streams (checked-table-name name "SignalTables.streams の列"))))
           (or tables streams)]}
  (#^ type signal)
  (setv #^ (get tuple #(str ...)) tables #()
        #^ (get tuple #(str ...)) streams #()))


(defclass SignalSourceUnreachable [RuntimeError]
  "合図の源(記録の置き場)に、繋ぎ直しても届かなかった — process を落として外の再起動に任せる(業務の Program には出さない)。")


(defrecord StreamStart
  "列 1 つの購読の始まり: stream = 列の名前 / after = 組み立ての時に読んだ末尾の番号(空の列は 0)/ signals = この列に結んだ合図の型。"
  {:tags {:context "records" :role "type"}}
  (#^ str stream)
  (#^ int after)
  (#^ (get tuple #(type ...)) signals))


(defrecord SourcePlan
  "組み立てた源 1 つ: subscriber = 購読者の名前(誤りの文で名指す)/ bindings = 合図の型と表・列の対 / tables = 結んだ表の名前
   (初めて出た順・重ねない)/ cursor = 表の変化の始まりの位置(結んだ表が無ければ None)/ streams = 結んだ列ごとの始まり。"
  {:tags {:context "records" :role "type"}}
  (#^ str subscriber)
  (#^ (get tuple #(SignalTables ...)) bindings)
  (#^ (get tuple #(str ...)) tables)
  (#^ (| WatchCursor None) cursor)
  (#^ (get tuple #(StreamStart ...)) streams))


(defk checked-bindings [bindings subscriber]
  {:pre [(: bindings tuple) (: subscriber str)] :post [(: % (get tuple #(SignalTables ...)))]}
  "組み立ての引数を確かめるため: 購読者の名前は空でない・対は 1 つ以上・要素は SignalTables・同じ合図の型は 1 度だけ(2 度出ると、
   どの組で起こすかが決まらない)。"
  (when (not subscriber)
    (raise (ValueError "購読者の名前は空でない文字列")))
  (when (not bindings)
    (raise (ValueError (.format "購読者 {!r} の源に結ぶ合図の型が無い(bindings が空)" subscriber))))
  (for [binding bindings]
    (when (not (isinstance binding SignalTables))
      (raise (TypeError (.format "購読者 {!r} の bindings の要素は SignalTables: {!r}" subscriber binding)))))
  (val signals (tuple (gfor binding bindings binding.signal)))
  (val repeated (tuple (gfor #(index signal) (enumerate signals) :if (in signal (cut signals index)) signal)))
  (when repeated
    (raise (ValueError (.format "購読者 {!r} の bindings に同じ合図の型が 2 度出る: {}"
                                subscriber (.join ", " (gfor signal repeated signal.__name__))))))
  bindings)


(defk first-seen [names]
  {:pre [(: names tuple)] :post [(: % tuple)]}
  "名前の並びを、初めて出た順のまま重ねずに返すため。"
  (tuple (gfor #(index name) (enumerate names) :if (not-in name (cut names index)) name)))


(defk reachable [ask subscriber names]
  {:pre [(: ask (| ListRows WatchChanges WatchEvents ReadStreamEnd)) (: subscriber str) (: names (get tuple #(str ...)))]
   :post [(: % (| Page NotIndexed Changes Reset EventsMoved EventsQuiet StreamEnd StreamEmpty))]}
  "記録の置き場への読み ask を撃ち、答えが Unreachable の間は RECONNECT-SECONDS 秒ずつ間を置いて RECONNECT-TRIES 回まで撃ち直すため
   (繋ぎ直し)。直らなければ、購読者の名前・表か列の名前・撃った回数・最後の detail を名指した SignalSourceUnreachable で落ちる。"
  (<- answered ask)
  (var answer answered)
  (var tries 1)
  (while (and (isinstance answer Unreachable) (< tries RECONNECT-TRIES))
    (<- (Delay RECONNECT-SECONDS))
    (<- again ask)
    (:= answer again)
    (:= tries (+ tries 1)))
  (when (isinstance answer Unreachable)
    (raise (SignalSourceUnreachable (.format "購読者 {!r} の合図の源(記録の {})に {} 回撃っても届かない: {}"
                                             subscriber (.join ", " names) tries answer.detail))))
  answer)


(defk start-cursor [subscriber tables]
  {:pre [(: subscriber str) (: tables (get tuple #(str ...)))] :post [(: % (| WatchCursor None))]}
  "表の変化の始まりの位置を読むため: 結んだ表の先頭の 1 つを ListRows で 1 頁(1 行)読み、その頁の epoch と sequence を位置にする
   (変更の番号は置き場で 1 本なので、どの表の頁でも同じ位置 — Page の註「最初の頁の値から WatchChanges を始めると取りこぼしが無い」)。
   結んだ表が無ければ None。"
  (when (not tables)
    (return None))
  (<- page (reachable (ListRows (get tables 0) :limit 1) subscriber tables))
  (match page
    (Page :epoch epoch :sequence sequence) (WatchCursor epoch sequence)
    _ (raise (TypeError (.format "購読者 {!r} の始まりの ListRows の答えは Page | Unreachable: {!r}" subscriber page)))))


(defk stream-starts [bindings subscriber]
  {:pre [(: bindings (get tuple #(SignalTables ...))) (: subscriber str)] :post [(: % (get tuple #(StreamStart ...)))]}
  "結んだ列ごとの始まりを読むため: 末尾の番号を ReadStreamEnd で読む(空の列は 0)。列に結んだ合図の型も添える。"
  (<- names (first-seen (tuple (gfor binding bindings name binding.streams name))))
  (var starts #())
  (for [name names]
    (<- end (reachable (ReadStreamEnd name) subscriber #(name)))
    (:= starts (+ starts #((StreamStart :stream name
                                        :after (if (isinstance end StreamEnd) end.sequence 0)
                                        :signals (tuple (gfor binding bindings :if (in name binding.streams) binding.signal)))))))
  starts)


(defk changed-rows [tables changes]
  {:pre [(: tables (get tuple #(str ...))) (: changes tuple)] :post [(: % (get tuple #(ChangedRow ...)))]}
  "変更の束のうち表 tables の変更の行(ChangedRow — 初めて出た順・束の中で同じ行が 2 度変わっても 1 つ)。"
  (val rows (tuple (gfor change changes
                         :if (in change.table tables)
                         (ChangedRow :table change.table :key (key-text change.key)))))
  (val seen (set))
  (tuple (gfor row rows :if (not-in row seen) :do (.add seen row) row)))


(defk signals-of [bindings changes]
  {:pre [(: bindings (get tuple #(SignalTables ...))) (: changes tuple)] :post [(: % tuple)]}
  "変更の束 → 合図の型ごとに 1 つの合図(bindings の順・keys = その型に結んだ表の変わった行)。結んだ表の変更が無い型は合図にしない。"
  (var signals #())
  (for [binding bindings]
    (<- rows (changed-rows binding.tables changes))
    (when rows
      (:= signals (+ signals #((binding.signal :keys rows))))))
  signals)


(defk publish-changes [plan]
  {:pre [(: plan SourcePlan)] :post [(: % None)]}
  "表の分の源の task: 位置から WatchChanges を出し(届かなければ繋ぎ直す)、変更の束を合図にして Publish し、位置を進めて繰り返す。
   Reset なら位置を WatchCursor(epoch floor) へ戻す(次の回で残っている変更を頭から合図にする)。止めるのは Cancel だけ。"
  (var cursor plan.cursor)
  (while True
    (<- answer (reachable (WatchChanges plan.tables cursor :timeout WATCH-SECONDS) plan.subscriber plan.tables))
    (match answer
      (Changes :items items :cursor moved)
        (do (<- signals (signals-of plan.bindings items))
            (for [signal signals]
              (<- (Publish signal)))
            (:= cursor moved))
      (Reset :epoch epoch :floor floor)
        (:= cursor (WatchCursor epoch floor))
      _ (raise (TypeError (.format "購読者 {!r} の WatchChanges の答えは Changes | Reset | Unreachable: {!r}" plan.subscriber answer)))))
  None)


(defk publish-appends [plan start]
  {:pre [(: plan SourcePlan) (: start StreamStart)] :post [(: % None)]}
  "列 1 つの源の task: 列の頭が after より進むのを WatchEvents で待ち(届かなければ繋ぎ直す)、進んだら末尾を ReadStreamEnd で読んで、
   列に結んだ型ごとに合図を 1 つ Publish し、after を末尾へ進めて繰り返す。静か(EventsQuiet)なら待ち直す。止めるのは Cancel だけ。"
  (var after start.after)
  (while True
    (<- moved (reachable (WatchEvents start.stream :after after :timeout WATCH-SECONDS) plan.subscriber #(start.stream)))
    (when (isinstance moved EventsMoved)
      (<- end (reachable (ReadStreamEnd start.stream) plan.subscriber #(start.stream)))
      ;; 頭が進んだ後に保持の期限で列が空になったら(StreamEmpty)、合図にする出来事が無い — 待ち直す。
      (when (isinstance end StreamEnd)
        (for [signal start.signals]
          (<- (Publish (signal :keys #((ChangedRow :table start.stream :key (str end.sequence)))))))
        (:= after end.sequence))))
  None)


(defk spawn-sources [plan]
  {:pre [(: plan SourcePlan)] :post [(: % tuple)]}
  "源の task を Spawn するため: 結んだ表があれば表の分を 1 つ、結んだ列ごとに 1 つ。"
  (var tasks #())
  (when plan.tables
    (<- watching (Spawn (publish-changes plan)))
    (:= tasks (+ tasks #(watching))))
  (for [start plan.streams]
    (<- appending (Spawn (publish-appends plan start)))
    (:= tasks (+ tasks #(appending))))
  tasks)


(defk stop-source [task]
  {:pre [(: task Task)] :post [(: % None)]}
  "源の task 1 つを止め、解け終わるまで待つため(源は Cancel でしか終わらないので、待ちの答えは TaskCancelledError)。"
  (<- (Cancel task))
  (try
    (<- (Wait task))
    (except [TaskCancelledError]
      None))
  None)


(defk wait-beside-sources [sources event-types]
  {:pre [(: sources tuple) (: event-types tuple)] :post [(: % "受けた合図")]}
  "本体の待ち 1 回に源の失敗を届けるため: 待ち(WaitForEvent)を子の task で外の購読者の列へ出し直し、源の task と Race する。合図が
   先なら合図を返す。源が先に落ちれば(源は Cancel でしか終わらない)、待ちの子を止めて源と同じ例外で落ちる。本体の task が Cancel
   されても(Race に TaskCancelledError が届く)、待ちの子を止めてから同じ例外で解ける。"
  (<- waiting (Spawn (WaitForEventEffect event-types)))
  (try
    (<- answer (Race waiting #* sources))
    (except [Exception]
      (<- (Cancel waiting))
      (raise)))
  answer)


(defhandler waits-beside-sources [#^ tuple sources]
  "包んだ本体の WaitForEvent を、源の task と並べて待たせるため(答えは外の購読者の列のまま — 待ちを子の task に出し直すだけ)。"
  {:tags {:context "records" :role "foundation"}}
  ;; 引数に残す理由: sources はこの包みが Spawn した源の task(走らせた時に出来る値)で、設定ではないので Ask では読めない。
  (WaitForEventEffect [event-types]
    (<- answer (wait-beside-sources sources event-types))
    (resume answer)))


(defk run-with-sources [plan body]
  {:tp [T] :pre [(: plan SourcePlan) (: body (| (get Program #(T object)) (get EffectBase T)))] :post [(: % T)]}
  "読んだ位置 plan で源の task を Spawn してから、本体をこの task のまま(Spawn せずに)走らせ、本体の答えを返すため(2 つの組み立て方の
   共有の包み)。本体の待ちには源の失敗が届く(waits-beside-sources)。本体が終われば、答えでも例外でも源の task を止める(源が先に
   落ちていれば、止める時の Wait がその例外を上げる — 待たずに終わった本体でも源の失敗を落とさない)。"
  (<- sources (spawn-sources plan))
  (try
    (<- answer (with-handlers [(waits-beside-sources sources)] body))
    (finally
      (for [task sources]
        (<- (stop-source task)))))
  answer)


(defk plan-of [bindings subscriber]
  {:pre [(: bindings (get tuple #(SignalTables ...))) (: subscriber str)] :post [(: % SourcePlan)]}
  "源 1 つの位置を読むため: 組み立ての引数を確かめ、結んだ表と列の購読の始まりの位置を読む(源の task を Spawn する前に読む — 本体の
   最初の読みより前に購読が始まる)。"
  (<- checked (checked-bindings bindings subscriber))
  (<- tables (first-seen (tuple (gfor binding checked name binding.tables name))))
  (<- cursor (start-cursor subscriber tables))
  (<- streams (stream-starts checked subscriber))
  (SourcePlan :subscriber subscriber :bindings checked :tables tables :cursor cursor :streams streams))


(defk run-signal-source [bindings subscriber body]
  {:tp [T] :pre [(: bindings (get tuple #(SignalTables ...))) (: subscriber str) (: body (| (get Program #(T object)) (get EffectBase T)))]
   :post [(: % T)]}
  "records-signal-handler の包み: 包んだ本体を走らせる頭で購読の始まりの位置を読み、源の task を Spawn してから本体を走らせるため。"
  (<- plan (plan-of bindings subscriber))
  (<- answer (run-with-sources plan body))
  answer)


(defclass BodyWrapper [partial]
  "本体を包む関数(partial)に、with-handlers が「本体を包む関数」として本体に当てる印を持たせるため(印が無いと effect の答え手として
   包み直す)。2 つの組み立て方が同じ印の付け方を使う。"
  (setv _doeff_is_handler_fn True))


;; 源の包み 1 つが本体の周りで出す effect の全部(records-signal-handler の宣言 __doeff_effects__ — 閉じの検の道具が工場の中を読めない
;; ので宣言する)。位置の読み(ListRows・ReadStreamEnd)・源の task(Spawn と、その中の WatchChanges・WatchEvents・ReadStreamEnd・
;; Publish・繋ぎ直しの間の Delay)・本体の待ちと源の Race(Spawn・Race・Cancel)・源の止め(Cancel・Wait)。本体の WaitForEvent は本体の
;; effect のまま外へ出る(子の task に出し直すだけ)ので数えない。実際に出す effect との一致は test_event_source_closure.py が確かめる。
(val SOURCE-EFFECTS #(ListRows ReadStreamEnd WatchChanges WatchEvents PublishEffect DelayEffect Spawn Race Cancel Wait))


;; 包む関数の型 = doeff の ProgramHandler(doeff/program.py の Callable[[object], Program[Any]] — subscribed_event_handler・timer_handler と
;; 同じ with-handlers の列に並ぶ型)。名の ProgramHandler は総称の別名で isinstance に渡せない(契約の実行時の確かめが TypeError)ので、
;; 同じ型を綴る — 型の引数を省いた Program は既定の Any(doeff の Program の型の引数の既定)で ProgramHandler と同じ。
(deff records-signal-handler [bindings subscriber]  ; defk にできない: with-handlers の列に呼びの字面で置く素の工場の関数 — 閉じの検の道具が宣言を読む形(#3104)
  {:pre [(: bindings (get tuple #(SignalTables ...))) (: subscriber str)] :post [(: % (get Callable #([object] Program)))]}
  "記録の変化を合図として発する源で本体を包む関数を作るため(素の工場 — Program ではなく、with-handlers の列に置く)。bindings =
   SignalTables の tuple(合図の型 → 表と列の名前の組)/ subscriber = 購読者の名前(誤りの文で名指す)。購読の始まりの位置は、包んだ
   本体を走らせる頭で(外の記録の handler の下で)読み、源の task を Spawn してから本体を走らせる — 包んだ本体の間だけ源の task が動く。
   引数の誤り(同じ合図の型を 2 度結ぶ等)も、その頭で名指して落ちる。"
  (BodyWrapper run-signal-source bindings subscriber))

;; 何にも答えず(__doeff_handles__ = ())、本体の周りで SOURCE-EFFECTS を出す、という宣言(doeff-effect-analyzer の body wrapper の読み)。
(setv records-signal-handler.__doeff_handles__ #()
      records-signal-handler.__doeff_effects__ SOURCE-EFFECTS)


(defrecord SignalSourceFactory
  "源の工場を土台が渡す値(Ask の鍵 — 鍵はこの型そのもの・答えはこの型の値・#3127): make = 結び(SignalTables の tuple)と
   購読者の名前から、本体を包む関数を作る工場。本番の土台は RECORDS-SIGNAL-SOURCE(記録の置き場の変化の待ちの long-poll —
   records-signal-handler)、模擬の土台は memory の置き場の書きで鳴る源(doeff_records.memory の memory-signal-source)を同じ鍵で渡す —
   組み立ての entry は鍵を問うて (make 結び 購読者) を with-handlers の列に置くだけで、本番と模擬の違いは土台の答えだけになる。"
  {:tags {:context "records" :role "type"}
   :check [(callable make)]}
  (#^ (get Callable #([(get tuple #(SignalTables ...)) str] (get Callable #([object] Program)))) make))


;; 本番の土台が鍵 SignalSourceFactory に答える値(記録の置き場の変化の待ちの long-poll で合図を発する源)。
(val RECORDS-SIGNAL-SOURCE (SignalSourceFactory :make records-signal-handler))


(defclass [(dataclass :frozen True)] ReadSignalSource [EffectBase]
  "この組で記録に答えている置き場の、源の工場を問う記録の effect(#3127)。答え = SignalSourceFactory。答えるのは記録の handler 自身(memory の
   handler = 自分の置き場の書きで鳴る模擬の源・HTTP の client と PostgreSQL = RECORDS-SIGNAL-SOURCE)— 源は必ず問うた所の記録の handler の置き場に
   結ばれる。Ask にしないのは、組の内側の設定の読み手(決まった鍵だけを持ち、知らない鍵を断る)に横取りされうるため — 記録の effect なら
   設定の読み手は触らない。")


(defk records-signal-source [bindings subscriber]
  {:pre [(: bindings (get tuple #(SignalTables ...))) (: subscriber str)] :post [(: % (get Callable #([object] Program)))]}
  "記録の変化を合図として発する源を組み立てるため(組み立ては Program — 外の記録の handler の下で走らせる。使い手が工場
   records-signal-handler へ付け替えるまで 1 版残す)。結んだ表と列の始まりの位置を組み立ての時に読んでから、工場と同じ包み方
   (run-with-sources)で本体を包む関数を返す — 包んだ本体の間だけ源の task が動く。"
  (<- plan (plan-of bindings subscriber))
  (BodyWrapper run-with-sources plan))
