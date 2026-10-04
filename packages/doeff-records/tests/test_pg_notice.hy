;;; PostgreSQL の置き場の変化の待ちが LISTEN / NOTIFY の呼び鈴で起きる事の失敗ケース(#3073)。
;;;   - 待っている間の書きは、その書きの刻に待ちを起こす(読み直しの間隔の刻まで待たない — 直す前は 0.2 秒ごとの読み直しで、
;;;     5.05 秒の書きに 5.2 秒で起きた)。
;;;   - 巻き戻した transaction の合図は呼び鈴を鳴らさない(commit した書きの合図だけが鳴らす)。
;;;   - 待ち受けの接続が切れたら、繋ぎ直して呼び鈴を鳴らす(切れていた間の通知は届かないので、待ち手に読み直させる)。
(require doeff-hy.macros [deftest defk <- val])
(import uuid)
(import doeff_core_effects.scheduler [Spawn Wait HANDLE-SWEEP-INTERVAL _SchedulerIntrospection])
(import doeff_core_effects.sql_effects [SqlQuery SqlTransaction SqlNotify SqlHangNotice SqlDropNotice SqlParam SqlRows SqlFailed])
(import doeff_time [Delay GetMonotonic WaitWithin])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.effects [ListRows PutRow WatchChanges])
(import doeff_records.values [Changes WatchCursor ExpectAbsent Written])
(import doeff_records.laws [LawHarness MAKER as-writer])
(import tests.interpreters [LawSetup DATABASE])

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


(defk notify-then-fail [channel]
  {:pre [(: channel str)] :post [(: % SqlRows)]}
  "合図を出してから engine が断る文を流す transaction の本体(transaction は巻き戻る)。"
  (<- (SqlNotify DATABASE channel))
  (<- answer (SqlQuery DATABASE "SELECT * FROM no_such_table_for_notice" #()))
  answer)


(defk notify-only [channel]
  {:pre [(: channel str)] :post [(: % None)]}
  "合図だけを出す transaction の本体(commit する)。"
  (<- (SqlNotify DATABASE channel))
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
  (<- bell (SqlHangNotice DATABASE channel))
  (<- aborted (SqlTransaction DATABASE (notify-then-fail channel)))
  (assert (isinstance aborted SqlFailed) aborted)
  (<- quiet (WaitWithin bell.future 0.5 :park True))
  (<- (SqlDropNotice DATABASE channel bell))
  (assert (is quiet None) quiet)
  ;; 対照: commit した transaction の合図は鳴らす。
  (<- again (SqlHangNotice DATABASE channel))
  (<- (SqlTransaction DATABASE (notify-only channel)))
  (<- rang (WaitWithin again.future 0.5 :park True))
  (assert (is rang True) rang))


;; 反例(#3508・#3494): 鳴らずに外した呼び鈴の外の promise が終わらないと、scheduler の promise の行が pending のまま残る(本番の記録の
;; service では、静かに時間が尽きた待ちごとに 1 行)。
(val UNRUNG-CYCLES (* 4 HANDLE-SWEEP-INTERVAL))


(deftest test-unrung-dropped-bells-do-not-grow-scheduler-promises
  {:interpreters ["pg" "pg-pooled"]}
  (<- channel (fresh-channel))
  (for [_ (range UNRUNG-CYCLES)]
    (<- bell (SqlHangNotice DATABASE channel))
    (<- (SqlDropNotice DATABASE channel bell)))
  (<- counts (_SchedulerIntrospection))
  ;; 直す前は外した呼び鈴が全部 pending のまま残る(UNRUNG-CYCLES 行 = 掃除の間隔の 4 倍)。直した後は、掃除と掃除の間に溜まる分(1 回の掛けで割り当てを数個使う)だけ。
  (assert (<= (get counts "promises") (* 2 HANDLE-SWEEP-INTERVAL)) counts))


(deftest test-a-dropped-listener-reconnects-and-rings
  {:interpreters ["pg" "pg-pooled"]}
  (<- channel (fresh-channel))
  (<- bell (SqlHangNotice DATABASE channel))
  ;; 待ち受けの接続(LISTEN を流した backend)を engine の側から切る。
  (<- cut SqlRows (SqlQuery DATABASE "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE query = :listen"
                            #((SqlParam :name "listen" :value (.format "LISTEN \"{}\"" channel)))))
  (assert (= (len cut.rows) 1) cut)
  ;; 切れた事で鳴る(park しない — 呼び鈴は本物の thread が鳴らすので、鳴るまで仮想の時計を止める)。
  (<- rang (WaitWithin bell.future 30.0))
  (assert (is rang True) rang)
  ;; 繋ぎ直した後は、合図で鳴る。
  (<- again (SqlHangNotice DATABASE channel))
  (<- (SqlNotify DATABASE channel))
  (<- rang-again (WaitWithin again.future 0.5 :park True))
  (assert (is rang-again True) rang-again))
