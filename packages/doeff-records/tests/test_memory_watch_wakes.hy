;; memory の置き場の WatchChanges の待ちは、読み直しの繰り返し(ポーリング)ではなく呼び鈴で起きる — 変更の列を動かす書きが待ち手を起こし、
;; 期限(timeout・保持の期限)は 1 回だけ鳴らす。答えの意味(変更の来た時に返る・来なければ timeout で空の Changes)は前と同じ。
;; 出自 = 使い手の模擬の検の実行時間の約 4 割が、仮想の時計の 0.05 秒ごとの読み直し(1 回の走行で
;; 約 19 万回)だった。確かめること: 読み直さない・同期の書き(handler の外から置き場を直に書く)と別の thread の書きでも起きる・
;; 待っている間に保持の期限が来た行の消えが timeout を待たずに届く。
(require doeff-hy.macros [defk <- val])
(import threading)
(import time)
(import datetime [datetime timezone])
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled Spawn Wait])
(import doeff_time [SimClock sim-time-handler sync-time-handler Delay GetTime])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.values [Changes Reset RowChanged RowRemoved WatchCursor ExpectAbsent ExpectVersion Written])
(import doeff_records.effects [PutRow ListRows WatchChanges])
(import doeff_records.admission [epoch-ms])
(import doeff_records.memory :as memory)
(import doeff_records.memory [MemoryStore memory-records-handler memory-put-row])
(import doeff_records.laws [LAW-SCHEMA MAKER TICKET-KEEP-SECONDS])

(val EPOCH (datetime 1970 1 1 :tzinfo timezone.utc))
;; 待ちの上限(秒)— 前の形(0.05 秒ごとの読み直し)ならこの間に 600 回走査した。
(val LONG-WAIT 30.0)


(defn run-on [store program]  ; defk にできない: 検の入口で Program を run する
  "仮想の時計と memory の置き場で program を回すため。"
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (memory-records-handler store MAKER)] program))))


(defn count-scans [monkeypatch]  ; defk にできない: pytest の monkeypatch で module の関数を包む(Program の外)
  "memory-watch-scan を包み、呼ばれた回数を数える箱(list の長さ)を返すため。"
  (setv calls [] original memory.memory-watch-scan)
  (defn counted [store ask]
    (.append calls ask)
    (original store ask))
  (.setattr monkeypatch memory "memory_watch_scan" counted)
  calls)


(defk seconds-now []
  {:pre [] :post [(: % float)]}
  "仮想の時計の今(起点からの秒)。"
  (<- now (GetTime))
  (.total-seconds (- now EPOCH)))


(defk idle-wait []
  {:pre [] :post [(: % tuple)]}
  "変更の来ない parts を LONG-WAIT 秒待つ。答え = #(答え 待ち終えた刻の秒)。"
  (<- start (ListRows "parts"))
  (<- answer (WatchChanges #("parts") (WatchCursor start.epoch start.sequence) :timeout LONG-WAIT))
  (<- at (seconds-now))
  #(answer at))


(defn test-an-idle-wait-scans-without-polling-and-ends-at-the-timeout [monkeypatch]  ; defk にできない: pytest の fixture を受ける検
  ;; 変更が来なければ timeout の刻に空の Changes が返る。その間に読み直さない(前の形は 600 回走査した)。
  (setv scans (count-scans monkeypatch))
  (setv #(answer at) (run-on (MemoryStore LAW-SCHEMA) (idle-wait)))
  (assert (and (isinstance answer Changes) (= answer.items #())) answer)
  (assert (= at LONG-WAIT) at)
  (assert (<= (len scans) 3) (len scans)))


(defk direct-write-later [store seconds]
  {:pre [(: store MemoryStore) (: seconds float)] :post [(: % Written)]}
  "seconds 秒後に、handler を通らず置き場の書きの関数を直に呼んで parts に 1 行書く(模擬の支度が置き場を直に書く形)。"
  (<- (Delay seconds))
  (<- now (GetTime))
  (memory-put-row store MAKER (PutRow "parts" #("late") (FrozenMap {"label" "l"}) (ExpectAbsent)) (epoch-ms now)))


(defk wait-for-direct-write [store]
  {:pre [(: store MemoryStore)] :post [(: % tuple)]}
  "5 秒後の直の書きを LONG-WAIT 秒の上限で待つ。答え = #(答え 起きた刻の秒)。"
  (<- start (ListRows "parts"))
  (<- writer (Spawn (direct-write-later store 5.0)))
  (<- answer (WatchChanges #("parts") (WatchCursor start.epoch start.sequence) :timeout LONG-WAIT))
  (<- at (seconds-now))
  (<- (Wait writer))
  #(answer at))


(defn test-a-direct-synchronous-write-wakes-the-waiter-at-its-instant []  ; defk にできない: 検の入口で Program を run する
  ;; handler の外の同期の書きでも待ち手が起き、書いた刻(5 秒)に変更が返る(timeout の 30 秒を待たない)。
  (setv store (MemoryStore LAW-SCHEMA))
  (setv #(answer at) (run-on store (wait-for-direct-write store)))
  (assert (and (isinstance answer Changes) (= (lfor item answer.items #((type item) item.key)) [#(RowChanged #("late"))])) answer)
  (assert (= at 5.0) at))


(defk wait-for-expiry []
  {:pre [] :post [(: % tuple)]}
  "tickets の行を終端にしてから、変更の無い tickets を保持の期限より長く待つ。答え = #(答え 起きた刻の秒 終端にした刻の秒)。"
  (<- (PutRow "tickets" #("g1" "t1") (FrozenMap {"owner" "o1"}) (ExpectAbsent)))
  (<- (Delay 1))
  (<- (PutRow "tickets" #("g1" "t1") (FrozenMap {"state" "done"}) (ExpectVersion 1)))
  (<- done-at (seconds-now))
  (<- start (ListRows "tickets"))
  (<- answer (WatchChanges #("tickets") (WatchCursor start.epoch start.sequence) :timeout (* 10 TICKET-KEEP-SECONDS)))
  (<- at (seconds-now))
  #(answer at done-at))


(defn test-a-row-expiring-during-the-wait-is-delivered-at-its-due-time []  ; defk にできない: 検の入口で Program を run する
  ;; 待っている間に保持の期限が来た行の消え(RowRemoved)は、期限の刻に届く(timeout の 600 秒を待たない — 誰も書かなくても起きる)。
  (setv #(answer at done-at) (run-on (MemoryStore LAW-SCHEMA) (wait-for-expiry)))
  (assert (and (isinstance answer Changes) (= (lfor item answer.items #((type item) item.key)) [#(RowRemoved #("g1" "t1"))])) answer)
  (assert (= at (+ done-at TICKET-KEEP-SECONDS)) #(at done-at)))


(defk watch-parts [timeout]
  {:pre [(: timeout float)] :post [(: % (| Changes Reset))]}
  "parts の今の頭から timeout 秒待つ。"
  (<- start (ListRows "parts"))
  (<- answer (WatchChanges #("parts") (WatchCursor start.epoch start.sequence) :timeout timeout))
  answer)


(defn test-a-write-from-another-thread-wakes-a-waiter-on-the-wall-clock []  ; defk にできない: 2 つの thread で置き場を共有する検
  ;; 実の時計で待つ thread を、別の thread の直の書きが起こす(timeout の 30 秒を待たない)。
  (setv store (MemoryStore LAW-SCHEMA)
        box []
        watcher (threading.Thread :target (fn [] (.append box (run (scheduled (with_handlers [(sync-time-handler) (memory-records-handler store MAKER)]
                                                                                                 (watch-parts LONG-WAIT))))))
                                  :daemon True)
        started (time.monotonic))
  (.start watcher)
  (while (not store.bells)
    (when (> (- (time.monotonic) started) 10)
      (raise (AssertionError "待ち手が呼び鈴を掛けない")))
    (time.sleep 0.01))
  (memory-put-row store MAKER (PutRow "parts" #("from-thread") (FrozenMap {"label" "t"}) (ExpectAbsent)) (epoch-ms (datetime.now timezone.utc)))
  (.join watcher 10)
  (assert (not (.is-alive watcher)) "待ち手が起きない")
  (assert (< (- (time.monotonic) started) 10) (- (time.monotonic) started))
  (setv answer (get box 0))
  (assert (and (isinstance answer Changes) (= (lfor item answer.items item.key) [#("from-thread")])) answer)
  (assert (= store.bells (set)) store.bells))


(defk tiny-wait []
  {:pre [] :post [(: % float)]}
  "浮動小数の誤差ほどの残り(5e-8 秒)で待つ。答え = 待ち終えた刻の秒。"
  (<- (watch-parts 5e-08))
  (<- at (seconds-now))
  at)


(defn test-a-positive-timeout-below-one-tick-still-lets-the-clock-move []  ; defk にできない: 検の入口で Program を run する
  ;; 残りの秒を渡して待ち直す呼び手が同じ刻で回り続けないよう、正の timeout は時計を少なくとも 1 刻み(1 マイクロ秒)進める。
  (setv at (run-on (MemoryStore LAW-SCHEMA) (tiny-wait)))
  (assert (= at 1e-06) at))
