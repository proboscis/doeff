;;; PostgreSQL の置き場の変化の待ちが LISTEN / NOTIFY の呼び鈴で起きる事の失敗ケース(#3073)。
;;;   - 待っている間の書きは、その書きの刻に待ちを起こす(読み直しの間隔の刻まで待たない — 直す前は 0.2 秒ごとの読み直しで、
;;;     5.05 秒の書きに 5.2 秒で起きた)。
;;;   - 巻き戻した transaction の合図は呼び鈴を鳴らさない(commit した書きの合図だけが鳴らす)。
;;;   - 待ち受けの接続が切れたら、繋ぎ直して呼び鈴を鳴らす(切れていた間の通知は届かないので、待ち手に読み直させる)。
;;; 名で絞る合図(#3688): 合図は名の重なる呼び鈴(と待つ名が None の呼び鈴)だけを鳴らす・待ち受けは自分の貸し出しの印の通知を鳴らさない
;;; (同じ process の呼び鈴は合図を流した答え手が鳴らし済み)・名の分からない合図(本文が読めない・名を載せると本文の上限に届く)は全部を
;;; 鳴らす。待ち受けが通知を処理し終えた事は、後から流した目印の合図で鳴る呼び鈴を、仮想の時計を止めて(park しない)待って確かめる
;;; (NOTIFY は commit の順に届く)。
(require doeff-hy.macros [deftest defk <- val])
(import gc)
(import uuid)
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled ExternalPromise Spawn Wait HANDLE-SWEEP-INTERVAL _SchedulerIntrospection])
(import doeff_core_effects.sql_effects [SqlQuery SqlTransaction SqlNotify SqlHangNotice SqlDropNotice SqlParam SqlRows SqlFailed])
(import doeff_core_effects.postgres_sql [NOTICE-STATEMENT postgres-sql-handler notice-payload])
(import doeff_time [Delay GetMonotonic WaitWithin SimClock sim-time-handler])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.effects [ListRows PutRow WatchChanges])
(import doeff_records.values [Changes WatchCursor ExpectAbsent Written])
(import doeff_records.laws [LawHarness MAKER as-writer])
(import tests.interpreters [LawSetup DATABASE session-dsn PG-DSN-VARIABLE pg-skip-reason postgres-connections])

(val PG-DSN (session-dsn PG-DSN-VARIABLE))
;; env が無ければ conftest が使い捨ての PostgreSQL を立てて置く(#2830)— 無いのは立てられなかった時で、その理由を名指す。
(val PG-SKIP-REASON (pg-skip-reason))

;; 書き手が書く刻(秒)— 直す前の読み直しの間隔(0.2 秒)の刻と重ならない値。
(val WRITE-AT 5.05)


(defk fresh-channel []
  {:pre [] :post [(: % str)]}
  "検ごとの通知の channel の名(同じ database の他の検の合図と混ざらない)。"
  (.format "probe_{}" (cut (. (uuid.uuid4) hex) 12)))


(defk late-write [harness]
  {:pre [(: harness LawHarness)] :post [(: % Written)]}
  "WRITE-AT 秒眠ってから 1 行書く(待ちの最中の書き)。"
  (<- (Delay WRITE-AT))
  (<- written (as-writer harness MAKER (PutRow "parts" #("late") (FrozenMap {"label" "l"}) (ExpectAbsent))))
  written)


;; 合図と呼び鈴の名(検の中だけの語)。
(val PROBE-TOPICS #("table:probe"))


(defk notify-then-fail [channel]
  {:pre [(: channel str)] :post [(: % SqlRows)]}
  "合図を出してから engine が断る文を流す transaction の本体(transaction は巻き戻る)。"
  (<- (SqlNotify DATABASE channel PROBE-TOPICS))
  (<- answer (SqlQuery DATABASE "SELECT * FROM no_such_table_for_notice" #()))
  answer)


(defk notify-only [channel topics]
  {:pre [(: channel str) (: topics (| tuple None))] :post [(: % None)]}
  "名 topics の合図だけを出す transaction の本体(commit する)。"
  (<- (SqlNotify DATABASE channel topics))
  None)


(deftest test-a-write-wakes-a-waiting-watch-at-its-own-instant
  {:interpreters ["pg" "pg-pooled"]}
  (<- harness (LawSetup))
  (<- start (as-writer harness MAKER (ListRows "parts")))
  (<- task (Spawn (late-write harness)))
  (<- began (GetMonotonic))
  (<- woke (as-writer harness MAKER (WatchChanges #("parts") (WatchCursor start.epoch start.sequence) :timeout 30.0)))
  (<- ended (GetMonotonic))
  (<- (Wait task))
  (assert (and (isinstance woke Changes) woke.items) woke)
  ;; 起きたのは書きの刻(読み直しの間隔の次の刻ではない)。
  (assert (< (abs (- (- ended began) WRITE-AT)) 1e-6) (- ended began)))


(deftest test-a-rolled-back-transaction-rings-no-bell
  {:interpreters ["pg" "pg-pooled"]}
  (<- channel (fresh-channel))
  (<- bell (SqlHangNotice DATABASE channel PROBE-TOPICS))
  (<- aborted (SqlTransaction DATABASE (notify-then-fail channel)))
  (assert (isinstance aborted SqlFailed) aborted)
  (<- quiet (WaitWithin bell.future 0.5 :park True))
  (<- (SqlDropNotice DATABASE channel bell))
  (assert (is quiet None) quiet)
  ;; 対照: commit した transaction の合図は鳴らす。transaction の答えを名指して確かめる(日次 t462 で、ここが答えを見ずに rang = None で
  ;; 赤 — 負荷の下で 2 つ目の transaction が落ちても、何が落ちたかが出なかった)。同じ process の呼び鈴は答え手が接続を返した後・答えを
  ;; 返す前に鳴らすので、答えを受けた時には鳴り終わっている — 待ちは鳴った呼び鈴を読むだけ(0.5 秒は仮想の時計の秒で、待ち受けの thread の
  ;; 届きを待たない)。
  (<- again (SqlHangNotice DATABASE channel PROBE-TOPICS))
  (<- committed (SqlTransaction DATABASE (notify-only channel PROBE-TOPICS)))
  (assert (is committed None) #("合図だけの transaction が commit しなかった" committed))
  (<- rang (WaitWithin again.future 0.5 :park True))
  (assert (is rang True) rang))


;; 反例(#3508・#3494): 鳴らずに外した呼び鈴の外の promise が終わらないと、scheduler の promise の行が pending のまま残る(本番の記録の
;; service では、静かに時間が尽きた待ちごとに 1 行)。
;; 反例(#3532): 掛けた呼び鈴と、掛けるために thread へ逃がした仕事の promise の handle が循環の参照に入っていると、掃除はその行を
;; 消せず、消えるかどうかが循環の回収(gc)の走る刻しだいになる(日次の回では同じ process の他の検で heap が大きく、回収がまれで赤)。
;; 掛け外しの間は循環の回収を止め、参照の数だけで handle が消える事を確かめる。
(val UNRUNG-CYCLES (* 4 HANDLE-SWEEP-INTERVAL))


(deftest test-unrung-dropped-bells-do-not-grow-scheduler-promises
  {:interpreters ["pg" "pg-pooled"]}
  (<- channel (fresh-channel))
  (gc.disable)
  (try
    (for [_ (range UNRUNG-CYCLES)]
      (<- bell (SqlHangNotice DATABASE channel PROBE-TOPICS))
      (<- (SqlDropNotice DATABASE channel bell)))
    (<- counts (_SchedulerIntrospection))
    (finally (gc.enable)))
  ;; 直す前は外した呼び鈴が全部 pending のまま残る(UNRUNG-CYCLES 行 = 掃除の間隔の 4 倍)。直した後は、掃除と掃除の間に溜まる分(1 回の掛けで割り当てを数個使う)だけ。
  (assert (<= (get counts "promises") (* 2 HANDLE-SWEEP-INTERVAL)) counts))


(deftest test-a-dropped-listener-reconnects-and-rings
  {:interpreters ["pg" "pg-pooled"]}
  (<- channel (fresh-channel))
  (<- bell (SqlHangNotice DATABASE channel PROBE-TOPICS))
  ;; 待ち受けの接続(LISTEN を流した backend)を engine の側から切る。
  (<- cut SqlRows (SqlQuery DATABASE "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE query = :listen"
                            #((SqlParam :name "listen" :value (.format "LISTEN \"{}\"" channel)))))
  (assert (= (len cut.rows) 1) cut)
  ;; 切れた事で鳴る(park しない — 呼び鈴は本物の thread が鳴らすので、鳴るまで仮想の時計を止める)。
  (<- rang (WaitWithin bell.future 30.0))
  (assert (is rang True) rang)
  ;; 繋ぎ直した後は、合図で鳴る。
  (<- again (SqlHangNotice DATABASE channel PROBE-TOPICS))
  (<- notified (SqlNotify DATABASE channel PROBE-TOPICS))
  (assert (is notified None) notified)
  (<- rang-again (WaitWithin again.future 0.5 :park True))
  (assert (is rang-again True) rang-again))


;; --- 名で絞る合図(#3688)---------------------------------------------------------------------------------------------------

(defk raw-notice [channel payload]
  {:pre [(: channel str) (: payload str)] :post [(: % None)]}
  "本文 payload の NOTIFY を transaction の外で手で流す(答え手の SqlNotify を通さない — 他の書き手の合図・自分の印の合図・読めない本文の代役)。"
  (<- sent SqlRows (SqlQuery DATABASE NOTICE-STATEMENT #((SqlParam :name "channel" :value channel) (SqlParam :name "payload" :value payload))))
  None)


(defk listener-caught-up [channel]
  {:pre [(: channel str)] :post [(: % None)]}
  "待ち受けが今までの通知を処理し終えるまで待つ(目印の名の呼び鈴を掛け、印の無い目印の合図を手で流し、鳴るまで仮想の時計を止めて待つ —
   NOTIFY は commit の順に届くので、目印が鳴った時には前の通知は処理済み)。"
  (<- marker (SqlHangNotice DATABASE channel #("marker")))
  (<- payload (notice-payload None #("marker")))
  (<- (raw-notice channel payload))
  (<- rang (WaitWithin marker.future 30.0))
  (assert (is rang True) #("目印の合図が待ち受けに届かなかった" rang))
  None)


(defk rung? [bell]
  {:pre [(: bell ExternalPromise)] :post [(: % bool)]}
  "呼び鈴がもう鳴ったか(鳴った呼び鈴は True で完了している — 鳴っていなければ仮想の時計で 0.5 秒待って None)。"
  (<- seen (WaitWithin bell.future 0.5 :park True))
  (is seen True))


(defk named-bells [origin]
  {:pre [(: origin str)] :post [(: % dict)]}
  "名の違う呼び鈴を掛け、同じ process の合図・自分の印の通知・他の書き手の合図・名の溢れた合図・読めない本文で、どれが鳴るかを読む。"
  (<- channel (fresh-channel))
  ;; 同じ process の合図(答え手が接続を返した後に鳴らす): 名の重なる呼び鈴と、待つ名が None の呼び鈴だけが鳴る。
  (<- a (SqlHangNotice DATABASE channel #("table:a")))
  (<- b (SqlHangNotice DATABASE channel #("table:b")))
  (<- every (SqlHangNotice DATABASE channel None))
  (<- committed (SqlTransaction DATABASE (notify-only channel #("table:a" "stream:c"))))
  (assert (is committed None) committed)
  (<- a-rang (rung? a))
  (<- every-rang (rung? every))
  ;; 自分の印の通知は待ち受けが鳴らさない(同じ process の呼び鈴は鳴らし済み — 掛け直した呼び鈴を 2 度起こさない)。
  (<- own-payload (notice-payload origin #("table:b")))
  (<- (raw-notice channel own-payload))
  (<- (listener-caught-up channel))
  (<- b-after-own (rung? b))
  ;; 他の書き手の合図(印が違う)は名の重なる呼び鈴を鳴らす。
  (<- foreign-payload (notice-payload "another-process" #("table:b")))
  (<- (raw-notice channel foreign-payload))
  (<- b-after-foreign (WaitWithin b.future 30.0))
  ;; 名を載せると本文の上限に届く合図は名を載せない = 全部に関わる合図(待つ名の重ならない呼び鈴も鳴る)。
  (<- d (SqlHangNotice DATABASE channel #("table:d")))
  (val many (tuple (gfor i (range 1000) (.format "table:many-{:04d}" i))))
  (<- overflowed (notice-payload "another-process" many))
  (<- (raw-notice channel overflowed))
  (<- d-rang (WaitWithin d.future 30.0))
  ;; 読めない本文(この答え手でない書き手の NOTIFY)は全部に関わる合図。
  (<- e (SqlHangNotice DATABASE channel #("table:e")))
  (<- (raw-notice channel ""))
  (<- e-rang (WaitWithin e.future 30.0))
  (for [bell #(a b every d e)]
    (<- (SqlDropNotice DATABASE channel bell)))
  {"a" a-rang "every" every-rang "b-after-own" b-after-own "b-after-foreign" b-after-foreign "overflowed" overflowed
   "d" d-rang "e" e-rang})


(deftest test-signals-ring-bells-by-name-once-per-process
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections))
  (try
    (val seen (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (postgres-sql-handler connections)]
                                             (named-bells connections.origin)))))
    (assert (get seen "a") seen)
    (assert (get seen "every") seen)
    (assert (not (get seen "b-after-own")) seen)
    (assert (is (get seen "b-after-foreign") True) seen)
    (assert (< (len (.encode (get seen "overflowed") "utf-8")) 8000) seen)
    (assert (not-in "topics" (get seen "overflowed")) seen)
    (assert (is (get seen "d") True) seen)
    (assert (is (get seen "e") True) seen)
    (finally (.close connections))))
