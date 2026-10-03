;;; 記録の変更を出来事の合図として発する源(#3077・設計 #3072)— 記録の変化の待ち(WatchChanges・WatchEvents)で受けた変化を、
;;; 合図の型ごとにまとめて Publish する。WaitForEvent には答えない — 待ちに答えるのは doeff-events の subscribed_event_handler の
;;; 購読者の列 1 つだけ(期限の TimerFired と記録の合図を、同じ WaitForEvent の 1 回で待てる)。
;;;
;;; 合図は「どこが変わったか」だけを運ぶ(状態の差分は運ばない — 受けた Program が記録を読み直す)。使い手は合図の型を dataclass で
;;; 宣言し、欄 keys(ChangedRow の tuple)を持たせる。どの合図の型をどの表・列の変化で起こすかは、組み立ての引数 bindings
;;; (SignalTables の tuple)の 1 か所に置き、Program には位置(cursor)・表と列の名前・購読者の名前を出さない。
;;;
;;; 組み立て(records-signal-source)は Program: 結んだ表を ListRows で 1 頁(1 行)読んだ頁の epoch と sequence、結んだ列の末尾
;;; (ReadStreamEnd・空なら 0)を購読の始まりの位置にしてから、本体を包む関数を返す(with-handlers の列に置ける)。包んだ本体を走らせると、
;;; 源の task(表の分 1 つ = WatchChanges の long-poll・列 1 つにつき 1 つ = WatchEvents の long-poll)を Spawn し、本体を task で走らせ、
;;; 本体が終われば源の task を止める(Cancel)。源の task か本体が落ちれば、残りを止めて同じ例外で落ちる。
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
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "records" :role "foundation"})
(import collections.abc [Callable])
(import dataclasses [dataclass fields is-dataclass])
(import functools [partial])
(import doeff [EffectBase Program])
(import doeff_core_effects.scheduler [Cancel Race Spawn Task TaskCancelledError Wait])
(import doeff_events.effects [Publish])
(import doeff_time [Delay])
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


(defk run-with-sources [plan body]
  {:tp [T] :pre [(: plan SourcePlan) (: body (| (get Program #(T object)) (get EffectBase T)))] :post [(: % T)]}
  "源の task を Spawn してから本体を task で走らせ、本体の答えを返すため。本体が終われば源の task を止める。源の task か本体が落ちれば
   (どちらが先でも Race が同じ例外を上げる)、残りの task に Cancel を出して同じ例外で落ちる。"
  (<- sources (spawn-sources plan))
  (<- running (Spawn body))
  (try
    (<- answer (Race running #* sources))
    (except [Exception]
      (for [task (+ #(running) sources)]
        (<- (Cancel task)))
      (raise)))
  (for [task sources]
    (<- (stop-source task)))
  answer)


(defk records-signal-source [bindings subscriber]
  {:pre [(: bindings tuple) (: subscriber str)] :post [(: % Callable)]}
  "記録の変化を合図として発する源を組み立てるため(組み立ては Program — 外の記録の handler の下で走らせる)。bindings = SignalTables の
   tuple(合図の型 → 表と列の名前の組)/ subscriber = 購読者の名前(誤りの文で名指す)。結んだ表と列の始まりの位置を読んでから、
   本体を包む関数(Program → Program・with-handlers の列に置ける)を返す — 包んだ本体の間だけ源の task が動く。"
  (<- checked (checked-bindings bindings subscriber))
  (<- tables (first-seen (tuple (gfor binding checked name binding.tables name))))
  (<- cursor (start-cursor subscriber tables))
  (<- streams (stream-starts checked subscriber))
  (val install (partial run-with-sources (SourcePlan :subscriber subscriber :bindings checked :tables tables :cursor cursor
                                                     :streams streams)))
  ;; with-handlers は、この印の在る関数を「本体を包む関数」として本体に当てる(印が無いと effect の答え手として包み直す)。
  (setattr install "_doeff_is_handler_fn" True)
  install)
