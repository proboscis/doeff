;;; 記録の変更を出来事の合図にする handler(#3077・設計 #3072)— WaitForEvent(合図の型 …)に、記録の変化の待ち WatchChanges で答える。
;;;
;;; 合図は「どこが変わったか」だけを運ぶ(状態の差分は運ばない — 受けた Program が記録を読み直す)。使い手は合図の型を dataclass で
;;; 宣言し、欄 keys(ChangedRow の tuple — 変わった行の表の名前と鍵の綴り)を持たせる。どの合図の型をどの表の変更で起こすかは、組み立ての
;;; 引数 bindings(SignalTables の tuple)の 1 か所に置き、Program には位置(cursor)・表の名前・購読者の名前を出さない。
;;;
;;; 組み立て(records-signal-handler)は Program: 結んだ表を ListRows で 1 頁(1 行)読み、その頁の epoch と sequence を購読の始まりの位置に
;;; してから handler を返す。返した handler を被せた Program の「読む → 処理 → WaitForEvent」の間の書きも、始まりが先なので取りこぼさない。
;;; handler は記録の置き場への接続を持たず、ListRows と WatchChanges を外へ出す — 外の記録の handler(memory・PostgreSQL・HTTP の口の
;;; どれでも)が答えるので、手元の模擬と本番の違いは外の記録の handler の接続先だけになる。
;;;
;;; WaitForEvent の待ち方は doeff-events の購読者の列(SubscriberQueue)を共有する: 列に当たる合図が在れば返し、無ければ WatchChanges
;;; (結んだ表すべて・今の位置・上限 WATCH-SECONDS 秒)を出し、返った変更の束を合図の型ごとに 1 つの合図にまとめて列に積み、位置を進めて、
;;; 当たる合図が出るまで繰り返す。列には待ち手を足さない(待つ task が自分で WatchChanges を出す — 同じ handler の下で 2 つの task が同時に
;;; 待つと、同じ変更から合図が 2 度積まれ得るが、合図は冪等なので許す)。
;;;   Reset       = 位置を WatchCursor(epoch floor) に戻して読み直す(残っている変更をもう一度合図にする — 合図は冪等なので重なってよい)。
;;;   Unreachable = handler の中で繋ぎ直す(RECONNECT-TRIES 回まで、RECONNECT-SECONDS 秒ずつ間を置いて撃ち直す)。直らなければ
;;;                 SignalSourceUnreachable で process を落とす(外の再起動が拾う)。使い手の Program には Unreachable を見せない。
;;; Publish は受け持たない(外へ渡す)— 記録の書きそのものが合図の源。
;;; 待ちの上限と繋ぎ直しの間は、記録の service との境界にあるこの handler の中だけの待ち(使い手の Program に時間の待ちを出さない)。
(require doeff-hy.macros [defk defhandler <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "records" :role "foundation"})
(import collections.abc [Callable])
(import dataclasses [dataclass fields is-dataclass])
(import doeff_events.effects [WaitForEventEffect])
(import doeff_events.handlers.memory [Empty SubscriberQueue])
(import doeff_time [Delay])
(import doeff_records.admission [key-text])
(import doeff_records.effects [ListRows WatchChanges])
(import doeff_records.values [Changes NotIndexed Page Reset Unreachable WatchCursor checked-table-name])

;; WatchChanges の待ちの上限(秒)— 変更が無ければこの秒で空の答えが返り、同じ位置から待ち直す(long-poll の 1 回の長さ)。
(val WATCH-SECONDS 30.0)
;; 置き場が Unreachable を返した時に撃つ回数(最初の 1 回を含む)と、撃ち直しの間(秒)。設定の口にはしない。
(val RECONNECT-TRIES 5)
(val RECONNECT-SECONDS 2.0)


(defrecord ChangedRow
  "合図が運ぶ「変わった行」1 つ: table = 表の名前 / key = 行の鍵の綴り(admission の key-text — 鍵の欄の順の文字列を JSON の配列に綴った
   文字列。tuple の鍵へは admission の key-from-text で戻す)。受けた Program はこの表のこの鍵の行を読み直す。"
  {:tags {:context "records" :role "type"}
   :check [(checked-table-name table "ChangedRow.table")
           (and (isinstance key str) key)]}
  (#^ str table)
  (#^ str key))


(defrecord SignalTables
  "合図の型 1 つと、その型の合図を起こす表の名前の組の対: signal = 使い手が宣言する合図の型(欄 keys を持つ dataclass — handler が
   (signal :keys 変わった行の tuple) で作る)/ tables = この型の合図を起こす表の名前(空でない tuple)。欄 keys を持たない型は作る時に止める。"
  {:tags {:context "records" :role "type"}
   :check [(isinstance signal type)
           (and (is-dataclass signal) (in "keys" (gfor declared (fields signal) declared.name)))
           (and (isinstance tables tuple) tables
                (all (gfor name tables (checked-table-name name "SignalTables.tables の表"))))]}
  (#^ type signal)
  (#^ (get tuple #(str ...)) tables))


(defclass SignalSourceUnreachable [RuntimeError]
  "合図の源(記録の置き場)に、繋ぎ直しても届かなかった — process を落として外の再起動に任せる(使い手の Program には出さない)。")


(defclass [(dataclass)] Subscription []
  "購読者 1 人の購読(handler 1 つの中だけで使う): subscriber = 購読者の名前(誤りの文で名指す)/ bindings = 合図の型と表の対 /
   tables = 結んだ表の名前(初めて出た順・重ねない — WatchChanges に渡す)/ queue = 合図の列(doeff-events の SubscriberQueue)/
   cursor = 変更の列のどこまでを合図にしたか(None = 結んだ表が無い — 発するだけの購読者)。cursor だけが待ちのたびに進む(frozen にしない)。"
  (#^ str subscriber)
  (#^ (get tuple #(SignalTables ...)) bindings)
  (#^ (get tuple #(str ...)) tables)
  (#^ SubscriberQueue queue)
  (#^ (| WatchCursor None) cursor))


(defk checked-bindings [bindings subscriber]
  {:pre [(: bindings tuple) (: subscriber str)] :post [(: % (get tuple #(SignalTables ...)))]}
  "組み立ての引数を確かめるため: 購読者の名前は空でない・要素は SignalTables・同じ合図の型は 1 度だけ(2 度出ると、どの表の組で
   起こすかが決まらない)。"
  (when (not subscriber)
    (raise (ValueError "購読者の名前は空でない文字列")))
  (for [binding bindings]
    (when (not (isinstance binding SignalTables))
      (raise (TypeError (.format "購読者 {!r} の bindings の要素は SignalTables: {!r}" subscriber binding)))))
  (val signals (tuple (gfor binding bindings binding.signal)))
  (val repeated (tuple (gfor #(index signal) (enumerate signals) :if (in signal (cut signals index)) signal)))
  (when repeated
    (raise (ValueError (.format "購読者 {!r} の bindings に同じ合図の型が 2 度出る: {}"
                                subscriber (.join ", " (gfor signal repeated signal.__name__))))))
  bindings)


(defk bound-tables [bindings]
  {:pre [(: bindings (get tuple #(SignalTables ...)))] :post [(: % (get tuple #(str ...)))]}
  "結んだ表の名前の組(初めて出た順・重ねない)— WatchChanges の tables と、始まりの位置を読む表。"
  (val names (tuple (gfor binding bindings name binding.tables name)))
  (tuple (gfor #(index name) (enumerate names) :if (not-in name (cut names index)) name)))


(defk reachable [ask subscriber tables]
  {:pre [(: ask (| ListRows WatchChanges)) (: subscriber str) (: tables (get tuple #(str ...)))] :post [(: % (| Page NotIndexed Changes Reset))]}
  "記録の置き場への読み ask を撃ち、答えが Unreachable の間は RECONNECT-SECONDS 秒ずつ間を置いて RECONNECT-TRIES 回まで撃ち直すため
   (繋ぎ直し)。直らなければ、購読者の名前・表・撃った回数・最後の detail を名指した SignalSourceUnreachable で落ちる。"
  (<- answered ask)
  (var answer answered)
  (var tries 1)
  (while (and (isinstance answer Unreachable) (< tries RECONNECT-TRIES))
    (<- (Delay RECONNECT-SECONDS))
    (<- again ask)
    (:= answer again)
    (:= tries (+ tries 1)))
  (when (isinstance answer Unreachable)
    (raise (SignalSourceUnreachable (.format "購読者 {!r} の合図の源(記録の表 {})に {} 回撃っても届かない: {}"
                                             subscriber (.join ", " tables) tries answer.detail))))
  answer)


(defk start-cursor [subscriber tables]
  {:pre [(: subscriber str) (: tables (get tuple #(str ...)))] :post [(: % (| WatchCursor None))]}
  "購読の始まりの位置を読むため: 結んだ表の先頭の 1 つを ListRows で 1 頁(1 行)読み、その頁の epoch と sequence を位置にする(変更の番号は
   置き場で 1 本なので、どの表の頁でも同じ位置 — Page の註「最初の頁の値から WatchChanges を始めると取りこぼしが無い」)。
   結んだ表が無ければ None(発するだけの購読者は待たない)。"
  (when (not tables)
    (return None))
  (<- page (reachable (ListRows (get tables 0) :limit 1) subscriber tables))
  (match page
    (Page :epoch epoch :sequence sequence) (WatchCursor epoch sequence)
    _ (raise (TypeError (.format "購読者 {!r} の始まりの ListRows の答えは Page | Unreachable: {!r}" subscriber page)))))


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


(defk watch-once [subscription]
  {:pre [(: subscription Subscription)] :post [(: % None)]}
  "WatchChanges を 1 回出し(届かなければ繋ぎ直す)、答えで列と位置を進めるため: Changes = 合図にして列に積み、位置を答えの cursor へ /
   Reset = 位置を WatchCursor(epoch floor) へ戻す(次の回で残っている変更を頭から合図にする)。"
  (<- answer (reachable (WatchChanges subscription.tables subscription.cursor :timeout WATCH-SECONDS)
                        subscription.subscriber subscription.tables))
  (match answer
    (Changes :items items :cursor cursor)
      (do (<- signals (signals-of subscription.bindings items))
          ;; 列に待ち手は居ない(この handler は待ち手を足さない — 頭の註)ので、offer は約束を返さない。
          (for [signal signals]
            (.offer subscription.queue signal))
          (setv subscription.cursor cursor))
    (Reset :epoch epoch :floor floor)
      (setv subscription.cursor (WatchCursor epoch floor))
    _ (raise (TypeError (.format "購読者 {!r} の WatchChanges の答えは Changes | Reset | Unreachable: {!r}"
                                 subscription.subscriber answer))))
  None)


;; 引数に残す理由: 購読者ごとに別の列と位置を持つ handler を、1 つの run(模擬の世界・1 つの process)の中に何人分も並べる。session の値は
;; handler の名でキーが決まり購読者の間で 1 つになる・最初の節で作られる(位置を Program が走る前に読めない)ので、組み立ての Program が
;; 読んだ位置と作った列をここで受ける。
(defhandler subscribed-signals [#^ Subscription subscription]
  ;; Publish ほかの effect は節が無いので外へ渡る(記録の書きそのものが合図の源)。
  (WaitForEventEffect [event-types]
    (val outside (.outside subscription.queue event-types))
    (when outside
      (raise (ValueError (.format "購読者 {!r} は購読の型の外を待った: {}(購読の型: {})— 外の型の合図は記録の変更から起きず、待ちが満たされない"
                                  subscription.subscriber
                                  (.join ", " (gfor kind outside kind.__name__))
                                  (or (.join ", " (gfor kind subscription.queue.event-types kind.__name__)) "なし")))))
    ;; 列に当たる合図が在れば返す。無ければ記録の変更を 1 回待って合図にし、列に積んでから取り直す(当たるまで繰り返す)。
    (var found (.take subscription.queue event-types))
    (while (isinstance found Empty)
      (<- (watch-once subscription))
      (:= found (.take subscription.queue event-types)))
    (resume found)))


(defk records-signal-handler [bindings subscriber]
  {:pre [(: bindings tuple) (: subscriber str)] :post [(: % Callable)]}
  "記録の変更を合図にする handler を組み立てるため(組み立ては Program — 外の記録の handler の下で走らせる)。bindings = SignalTables の
   tuple(合図の型 → 表の名前の組)/ subscriber = 購読者の名前(誤りの文で名指す)。結んだ表を ListRows で 1 頁読んだ位置を購読の始まりに
   してから、WaitForEvent に答える handler(Program → Program の関数)を返す。bindings が空なら発するだけ(どの型を待っても ValueError)。"
  (<- checked (checked-bindings bindings subscriber))
  (<- tables (bound-tables checked))
  (<- start (start-cursor subscriber tables))
  (subscribed-signals (Subscription :subscriber subscriber
                                    :bindings checked
                                    :tables tables
                                    :queue (SubscriberQueue (tuple (gfor binding checked binding.signal)))
                                    :cursor start)))
