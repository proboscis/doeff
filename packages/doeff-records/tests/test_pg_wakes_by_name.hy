;;; PostgreSQL の置き場の書きが long-poll の待ち手を起こす形の失敗ケース(#3688)。直す前は、書きの COMMIT が待ち手を
;;; 表や列の名で絞らずに全部・2 回ずつ(同じ process の呼び鈴と、自分の NOTIFY を受けた待ち受け)・接続を返す前に起こしていた:
;;;   - 関係の無い待ち手(書かない表・書かない列を待つ)は、書きで起きない(読み直しの数 0)。直す前は待ち手 1 本が書き 1 回で 0.8〜1.3 回
;;;     読み直した(書き 25 回で 30 本が 740〜800 回)。
;;;   - 関係の無い待ち手を 30 本置いても、書き 1 回の秒は待ち手 0 本の時と同じ幅(中央値の比が WRITE-SLOWDOWN 倍以内)。直す前は、起こした
;;;     待ち手の読み直しの後ろに書き手の残り(接続を返す)が並び、この検の形で 44 倍に伸びた。
;;;   - 関係のある待ち手は、書き 1 回で 1 回だけ起きる(自分の NOTIFY で起き直さない)。
;;;   - 取りこぼしが無い: 書き手 3 本の書きと追記に並べて待ち手が位置の続きから読み続け、全部の変更を位置の順に、書いた刻(仮想の時計)に
;;;     受ける(呼び鈴を掛けてから読む順を崩すと、掛ける前の書きを時間切れまで拾わない)。直す前も緑 — 直しで崩さない事の見張り。
;;; 待ち手の読み直しは、置き場の読みの SqlQuery(変更の列の読み = 頼んだ表・追記の列の読み = 列)を待つ名ごとに数える代役 scan-tap で数える。
;;; 仮想の時計の下で回す。待ち受けの thread が鳴らす合図(NOTIFY)を数え漏らさないように、数える前に実の時間を SETTLE-SECONDS 置いてから
;;; 仮想の時計を 1 刻み進める(時計が進むのは全部の task が眠った後 — 起きた待ち手の読み直しは数え終わっている)。
;;; env DOEFF_RECORDS_TEST_PG_DSN が無ければ conftest が使い捨ての PostgreSQL を立てて置く(#2830)。立てられなければ理由を名指して skip。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(import statistics)
(import time)
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled Spawn Wait])
(import doeff_core_effects.sql_effects [SqlQuery])
(import doeff_core_effects.postgres_sql [postgres-sql-handler])
(import doeff_time [SimClock sim-time-handler Delay GetMonotonic])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.values [ExpectAny ExpectAbsent WatchCursor Changes Reset Unreachable Written Appended EventsMoved EventsQuiet Events])
(import doeff_records.effects [PutRow ListRows WatchChanges WatchEvents AppendEvent ReadEvents])
(import doeff_records.laws [MAKER])
(import doeff_records.pg [pg-records-handler drop-records-tables])
(import tests.interpreters [session-dsn PG-DSN-VARIABLE pg-skip-reason ORIGIN-HOST postgres-connections fresh-prefix run-sql prepared-store])

(val PG-DSN (session-dsn PG-DSN-VARIABLE))
;; env が無ければ conftest が使い捨ての PostgreSQL を立てて置く(#2830)— 無いのは立てられなかった時で、その理由を名指す。
(val PG-SKIP-REASON (pg-skip-reason))

;; 関係の無い待ち手の数(本番の記録の service の client の接続 29 本に揃える)と、その待ちの秒(検の間は時間切れにしない長さ — 終わりに
;; 仮想の時計を進めて返す)。
(val IDLE-WAITERS 30)
(val IDLE-SECONDS 3600.0)
;; 書きの秒を比べる回数と、比べる前に捨てる慣らしの回数。
(val TIMED-WRITES 20)
(val WARM-WRITES 5)
;; 書き 1 回の秒の中央値の比の上限(待ち手 30 本 / 0 本)。この検の形(仮想の時計・VM の上の PutRow)の手元の測り(zeus・load 67〜130):
;; 直す前は 44 倍、直した後は 1.1〜3.4 倍(眠っている待ち手を数えない書きでも、機体の混み方で揺れる — 同じ形を実の時計で測っても 1.1〜3.4)。
;; 1.5 倍では混んだ機体の日次で揺れて赤になるので、直した後の揺れの上限の 1.5 倍ほど・直す前の 9 分の 1 に置く。数の断言(読み直し 0)が
;; 主で、この比は「起こした待ち手の後ろに書き手が並ぶ」伸びの再発の見張り。
(val WRITE-SLOWDOWN 5.0)
;; 待ち受けの thread が NOTIFY を受けて呼び鈴を鳴らすまでに置く実の秒(手元の PostgreSQL では 1 ms 未満で届く)。
(val SETTLE-SECONDS 0.2)
;; 関係のある待ち手の検の書きの回数・取りこぼしの検の書き手の数と 1 本あたりの書きの回数(この file を混んだ機体でも 60 秒の内に回す大きさ)。
(val FOLLOWED-WRITES 8)
(val WRITERS 3)
(val WRITES-PER-WRITER 10)
;; 関係のある待ち手の 1 回の待ちの秒(取りこぼせば時間切れで拾い、受けた刻が書いた刻より後になる)。
(val FOLLOW-SECONDS 30.0)


(defclass ScanCounts []
  "待ちの読み直しの数え(scan-tap が数える — 走らせる VM の thread だけが書く): changes = 頼んだ表の組 → 変更の列の読みの数 / events = 列 →
   追記の列の読みの数。"
  (defn __init__ [self]  ; defk にできない: 検の資源の class の初期化
    (setv self.changes {} self.events {}))
  (defn note [self #^ str statement #^ tuple params]  ; defk にできない: 代役の答え手の節が同期に数える
    "置き場の読みの文 1 つを数える(変更の列の読み = 引数 t0, t1 … の表の組・追記の列の読み = 引数 ledger の列)。他の文は数えない。"
    (setv values (dfor p params p.name p.value))
    (cond
      (in "row_changes AS c" statement)
        (do (setv tables (tuple (sorted (gfor #(name value) (.items values) :if (.startswith name "t") value))))
            (setv (get self.changes tables) (+ (.get self.changes tables 0) 1)))
      (and (in "append_rows AS old" statement) (in "ORDER BY old.seq" statement))
        (do (setv stream (get values "ledger"))
            (setv (get self.events stream) (+ (.get self.events stream 0) 1)))))
  (defn snapshot [self]  ; defk にできない: 検の本体が同期に読む
    "今の数えの写し(changes と events の dict の組)。"
    #((dict self.changes) (dict self.events))))


(defhandler scan-tap [#^ ScanCounts counts]
  ;; 引数に残す理由: 数えは検ごとに作る資源。
  "置き場の読みの SqlQuery を待ちの読み直しとして数え、外側の本物の答え手へ回す代役。"
  {:tags {:context "records" :role "program"}}
  (SqlQuery [database statement params]
    (.note counts statement params)
    (reperform effect)))


(defn run-on [connections store counts program]
  "本物の答え手(postgres-sql-handler)と数えの代役の上で、置き場 store の書き手 maker として program を仮想の時計で 1 回走らせる。"
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (postgres-sql-handler connections) (scan-tap counts)
                                  (pg-records-handler store MAKER ORIGIN-HOST)]
                                 program))))


(defk settled []
  {:pre [] :post [(: % None)]
   :tags {:context "records" :role "program"}}
  "待ち受けの thread が鳴らす合図を数え終える所まで進むため(頭の註): 実の SETTLE-SECONDS を置いてから、仮想の時計を 1 刻み進める。"
  (time.sleep SETTLE-SECONDS)
  (<- (Delay 0.001))
  None)


(defk idle-changes [table cursor]
  {:pre [(: table str) (: cursor WatchCursor)] :post [(: % (| Changes Reset Unreachable))]
   :tags {:context "records" :role "program"}}
  "表 table の変更を IDLE-SECONDS まで待つ(書かない表 tickets なら関係の無い待ち手)。"
  (<- answer (WatchChanges #(table) cursor :timeout IDLE-SECONDS))
  answer)


(defk idle-events []
  {:pre [] :post [(: % (| EventsMoved EventsQuiet Unreachable))]
   :tags {:context "records" :role "program"}}
  "書かない列 pulses を IDLE-SECONDS まで待つ(関係の無い待ち手)。"
  (<- answer (WatchEvents "pulses" :after 0 :timeout IDLE-SECONDS))
  answer)


(defk idle-waiters [cursor]
  {:pre [(: cursor WatchCursor)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "関係の無い待ち手を IDLE-WAITERS 本(半分は表 tickets・半分は列 pulses)起こして task の組を返すため。"
  (var tasks #())
  (for [i (range IDLE-WAITERS)]
    (<- task (Spawn (if (= (% i 2) 0) (idle-changes "tickets" cursor) (idle-events))))
    (:= tasks (+ tasks #(task))))
  tasks)


(defk ended [tasks]
  {:pre [(: tasks tuple)] :post [(: % None)]
   :tags {:context "records" :role "program"}}
  "関係の無い待ち手を時間切れまで仮想の時計を進めて返させ、終わりを待つため。"
  (<- (Delay (+ IDLE-SECONDS 1)))
  (for [task tasks]
    (<- (Wait task)))
  None)


(defk write-seconds [label count]
  {:pre [(: label str) (: count int)] :post [(: % list)]
   :tags {:context "records" :role "program"}}
  "表 parts へ 1 行ずつ count 回書き、1 回ごとの実の秒の列を返すため。"
  (var seconds [])
  (for [i (range count)]
    (val began (time.perf-counter))
    (<- written (PutRow "parts" #((.format "{}-{}" label i)) (FrozenMap {"label" label}) (ExpectAny)))
    (:= seconds (+ seconds [(- (time.perf-counter) began)]))
    (assert (isinstance written Written) written))
  seconds)


(defk unrelated-scenario [counts]
  {:pre [(: counts ScanCounts)] :post [(: % dict)]
   :tags {:context "records" :role "program"}}
  "待ち手 0 本で書きの秒を測り、関係の無い待ち手を置いて同じ書きの秒と、その書きの間の待ち手の読み直しの数を測るため。"
  (<- start (ListRows "tickets"))
  (<- (write-seconds "warm" WARM-WRITES))
  (<- quiet (write-seconds "quiet" TIMED-WRITES))
  (<- tasks (idle-waiters (WatchCursor start.epoch start.sequence)))
  (<- (settled))
  (val before (.snapshot counts))
  (<- busy (write-seconds "busy" TIMED-WRITES))
  (<- (settled))
  (val after (.snapshot counts))
  (<- (ended tasks))
  {"quiet" quiet "busy" busy "before" before "after" after})


(deftest test-unrelated-waiters-neither-wake-nor-slow-a-write
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections 9))
  (val store (prepared-store connections (fresh-prefix)))
  (val counts (ScanCounts))
  (try
    (val seen (run-on connections store counts (unrelated-scenario counts)))
    (val changes-before (get seen "before" 0))
    (val events-before (get seen "before" 1))
    (val changes-after (get seen "after" 0))
    (val events-after (get seen "after" 1))
    ;; 待ち手は掛けてから 1 度だけ読んで眠っている(数えの台が正しく数えている事の対照)。
    (assert (= changes-before {#("tickets") (// IDLE-WAITERS 2)}) changes-before)
    (assert (= events-before {"pulses" (// IDLE-WAITERS 2)}) events-before)
    (val quiet (statistics.median (get seen "quiet")))
    (val busy (statistics.median (get seen "busy")))
    (val timing {"待ち手 0 本の中央値 ms" (round (* 1000 quiet) 2) "待ち手 30 本の中央値 ms" (round (* 1000 busy) 2)
                 "比" (round (/ busy quiet) 2)})
    ;; 書かない表・書かない列の待ち手は、表 parts への書きで 1 度も読み直さない。
    (assert (= #(changes-after events-after) #(changes-before events-before))
            {"書きの回数" TIMED-WRITES "前" #(changes-before events-before) "後" #(changes-after events-after) "書きの秒" timing})
    (assert (<= (/ busy quiet) WRITE-SLOWDOWN) timing)
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))


(defk follow-changes [cursor wanted]
  {:pre [(: cursor WatchCursor) (: wanted int)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "表 parts の変更を位置の続きから待って読み続け、wanted 個を受けたら #(鍵 位置 受けた刻) の tuple を返すため(関係のある待ち手)。"
  (var at cursor)
  (var seen #())
  (while (< (len seen) wanted)
    (<- answer (WatchChanges #("parts") at :timeout FOLLOW-SECONDS))
    (<- now (GetMonotonic))
    (assert (isinstance answer Changes) answer)
    (:= seen (+ seen (tuple (gfor item answer.items #((get item.key 0) item.sequence now)))))
    (:= at answer.cursor))
  seen)


(defk follow-events [after wanted]
  {:pre [(: after int) (: wanted int)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "列 journal の頭が進むのを待って続きを読み続け、wanted 個を受けたら #(冪等キー 番号 受けた刻) の tuple を返すため(関係のある待ち手)。"
  (var last after)
  (var seen #())
  (while (< (len seen) wanted)
    (<- moved (WatchEvents "journal" :after last :timeout FOLLOW-SECONDS))
    (<- now (GetMonotonic))
    (assert (isinstance moved EventsMoved) moved)
    (<- read (ReadEvents "journal" :after last :limit 1000))
    (assert (isinstance read Events) read)
    (:= seen (+ seen (tuple (gfor event read.items #(event.idempotency-key event.sequence now)))))
    (:= last read.last-sequence))
  seen)


(defk followed-scenario [counts]
  {:pre [(: counts ScanCounts)] :post [(: % dict)]
   :tags {:context "records" :role "program"}}
  "関係の無い待ち手と関係のある待ち手を置き、表 parts へ 1 行ずつ書くたびに合図を数え終えて、関係のある待ち手の読み直しを数えるため。
   前半 = 変更を受けて待ち直す待ち手(書き FOLLOWED-WRITES 回)・後半 = 変更の無い書き(在る鍵への ExpectAbsent — 衝突)で起きても読み直して
   待ち続ける待ち手(書き FOLLOWED-WRITES 回 — 自分の NOTIFY でもう 1 回起きれば読み直しが増える)。"
  (<- start (ListRows "parts"))
  (val cursor (WatchCursor start.epoch start.sequence))
  (<- tasks (idle-waiters cursor))
  (<- follower (Spawn (follow-changes cursor FOLLOWED-WRITES)))
  (<- (settled))
  (val before (.snapshot counts))
  (for [i (range FOLLOWED-WRITES)]
    (<- (PutRow "parts" #((.format "followed-{}" i)) (FrozenMap {"label" "f"}) (ExpectAny)))
    (<- (settled)))
  (val after (.snapshot counts))
  (<- seen (Wait follower))
  (<- head (ListRows "parts"))
  (<- staying (Spawn (idle-changes "parts" (WatchCursor head.epoch head.sequence))))
  (<- (settled))
  (val unchanged-before (.snapshot counts))
  (for [_ (range FOLLOWED-WRITES)]
    (<- refused (PutRow "parts" #("followed-0") (FrozenMap {"label" "g"}) (ExpectAbsent)))
    (assert (= (. (type refused) __name__) "Conflict") refused)
    (<- (settled)))
  (val unchanged-after (.snapshot counts))
  (<- (ended (+ tasks #(staying))))
  {"before" before "after" after "seen" seen "unchanged-before" unchanged-before "unchanged-after" unchanged-after})


(deftest test-a-relevant-waiter-wakes-once-per-write
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections 9))
  (val store (prepared-store connections (fresh-prefix)))
  (val counts (ScanCounts))
  (try
    (val seen (run-on connections store counts (followed-scenario counts)))
    (val changes-before (get seen "before" 0))
    (val changes-after (get seen "after" 0))
    (assert (= (len (get seen "seen")) FOLLOWED-WRITES) (get seen "seen"))
    ;; 前半: 書き 1 回ごとに、関係のある待ち手は 1 回起きて読み直し(変更を受ける)、続きの待ちを掛けて 1 回読む = 読み 2 回(最後の書きの後は
    ;; 待ち直さない)。起きなければ変更を受けず、2 回起きれば読みが増える。
    (val scans (- (.get changes-after #("parts") 0) (.get changes-before #("parts") 0)))
    (assert (= scans (- (* 2 FOLLOWED-WRITES) 1)) {"書きの回数" FOLLOWED-WRITES "読み直しの数" scans})
    ;; 後半: 変更の無い書き 1 回で、待ち続ける待ち手は多くて 1 回読み直す(自分の NOTIFY で起き直さない)。
    (val unchanged (- (.get (get seen "unchanged-after" 0) #("parts") 0) (.get (get seen "unchanged-before" 0) #("parts") 0)))
    (assert (<= unchanged FOLLOWED-WRITES) {"変更の無い書きの回数" FOLLOWED-WRITES "読み直しの数" unchanged})
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))


(defk written-by [writer count]
  {:pre [(: writer int) (: count int)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "書き手 writer として表 parts への 1 行と列 journal への追記を count 回交互に撃ち、#(鍵 書いた刻) の tuple を返すため。書きの間は
   仮想の時計で 0〜2 ミリ秒空ける(書き手ごとにずらし、待ち手の掛けと読みの間に書きが入る並びを作る)。"
  (var written #())
  (for [i (range count)]
    (val key (.format "w{}-{}" writer i))
    (<- row (PutRow "parts" #(key) (FrozenMap {"label" key}) (ExpectAny)))
    (assert (isinstance row Written) row)
    (<- appended (AppendEvent "journal" key {"n" i}))
    (assert (isinstance appended Appended) appended)
    (<- now (GetMonotonic))
    (:= written (+ written #(#(key now))))
    (<- (Delay (* 0.001 (% (+ i writer) 3)))))
  written)


(defk raced-scenario []
  {:pre [] :post [(: % dict)]
   :tags {:context "records" :role "program"}}
  "書き手 WRITERS 本と、表 parts の待ち手・列 journal の待ち手を並べて走らせ、書いた鍵と刻・受けた鍵と位置と刻を返すため。"
  (<- start (ListRows "parts"))
  (val total (* WRITERS WRITES-PER-WRITER))
  (<- rows (Spawn (follow-changes (WatchCursor start.epoch start.sequence) total)))
  (<- events (Spawn (follow-events 0 total)))
  (var writers #())
  (for [w (range WRITERS)]
    (<- task (Spawn (written-by w WRITES-PER-WRITER)))
    (:= writers (+ writers #(task))))
  (var written #())
  (for [task writers]
    (<- one (Wait task))
    (:= written (+ written one)))
  (<- seen-rows (Wait rows))
  (<- seen-events (Wait events))
  {"written" written "rows" seen-rows "events" seen-events})


(deftest test-waiters-miss-no-change-while-writes-race
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections 9))
  (val store (prepared-store connections (fresh-prefix)))
  (try
    (val seen (run-on connections store (ScanCounts) (raced-scenario)))
    (val written (dict (get seen "written")))
    (for [name #("rows" "events")]
      (val got (get seen name))
      ;; 全部を 1 度ずつ、位置の順に受ける。
      (assert (= (sorted (gfor #(key _ _) got key)) (sorted written)) #(name got))
      (val positions (lfor #(_ position _) got position))
      (assert (= positions (sorted (set positions))) #(name positions))
      ;; 書いた刻に受ける(取りこぼした変更は、次の書きか時間切れの刻まで受けない)。
      (val late (lfor #(key _ at) got :if (!= at (get written key)) #(key (get written key) at)))
      (assert (not late) #(name late)))
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))
