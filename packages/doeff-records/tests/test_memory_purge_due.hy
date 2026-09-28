;; memory の置き場の保持の刈り(purge-expired)は、消え得る刻(MemoryStore.purge-due-ms)より前なら行と出来事を走査しない。
;; 出自 = 2026-09-27 の保持の刈りの性能の直し: 刈りはどの操作の前にも呼ばれ、期限つきの列が 1 本でも在ると毎回すべての出来事を読み直していた —
;; 出来事 1 万を積む模擬の筋書き 1 つが 5 分を越えた(出来事の数の 2 乗)。消える物は変わらない(法 12 などの保持の法が memory と pg で守る)。
(require doeff-hy.macros [defk <- val var])
(import dataclasses)
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [SimClock sim-time-handler Delay])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.values [StreamDecl KeepFor ExpectAbsent ExpectVersion Row Missing RowRemoved])
(import doeff_records.effects [AppendEvent ReadEvents PutRow ReadRow ListRows])
(import doeff_records.memory :as memory)
(import doeff_records.memory [MemoryStore memory-records-handler])
(import doeff_records.laws [LAW-SCHEMA MAKER TICKET-KEEP-SECONDS PAIR-KEEP-SECONDS])

;; 期限つきの列 pulses(60 秒)を足した宣言。
(val PULSE-KEEP-SECONDS 60)
(val SCHEMA
  (dataclasses.replace LAW-SCHEMA
    :streams (FrozenMap {"journal" (get LAW-SCHEMA.streams "journal")
                         "pulses" (StreamDecl :name "pulses" :writers #(MAKER) :retention (KeepFor PULSE-KEEP-SECONDS))})))
(val EVENT-COUNT 2000)


(defn count-scans [monkeypatch]  ; defk にできない: pytest の monkeypatch で module の関数を包む(Program の外)
  "purge-expired-scan を包み、呼ばれた回数を数える箱(list の長さ)を返すため。"
  (setv calls [] original memory.purge-expired-scan)
  (defn counted [store now-ms]
    (.append calls now-ms)
    (original store now-ms))
  (.setattr monkeypatch memory "purge_expired_scan" counted)
  calls)


(defn run-on [store program]  ; defk にできない: 検の入口で Program を run する
  "仮想の時計と memory の置き場で program を回すため。"
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (memory-records-handler store MAKER)] program))))


(defk pulses-then-wait [count wait-seconds]
  {:pre [(: count int) (: wait-seconds (| int float))] :post [(: % tuple)]}
  "pulses へ count 個を積み、wait-seconds 待ってから読む。答え = #(待つ前に読めた数 待った後に読めた数)。"
  (for [i (range count)]
    (<- (AppendEvent "pulses" (.format "p{}" i) {"n" i})))
  (<- before (ReadEvents "pulses" :limit (+ count 1)))
  (<- (Delay wait-seconds))
  (<- after (ReadEvents "pulses" :limit (+ count 1)))
  #((len before.items) (len after.items)))


(defn test-appends-before-the-due-time-do-not-rescan-the-store [monkeypatch]  ; defk にできない: pytest の fixture を受ける検
  ;; 2000 個を積んで読む間(期限の前)は 1 度も走査しない。
  (setv scans (count-scans monkeypatch))
  (setv got (run-on (MemoryStore SCHEMA) (pulses-then-wait EVENT-COUNT 1)))
  (assert (= got #(EVENT-COUNT EVENT-COUNT)) got)
  (assert (= scans []) (len scans)))


(defn test-the-events-still-expire-when-the-due-time-comes [monkeypatch]  ; defk にできない: pytest の fixture を受ける検
  ;; 期限を過ぎた最初の操作で 1 度だけ走査し、全部消える。その後は消え得る物が無いので走査しない。
  (setv scans (count-scans monkeypatch) store (MemoryStore SCHEMA))
  (setv got (run-on store (pulses-then-wait EVENT-COUNT (+ PULSE-KEEP-SECONDS 1))))
  (assert (= got #(EVENT-COUNT 0)) got)
  (assert (= (len scans) 1) scans)
  (assert (is store.purge-due-ms None) store.purge-due-ms))


(defk ticket-closed-late [open-seconds read-offsets]
  {:pre [(: open-seconds (| int float)) (: read-offsets tuple)] :post [(: % list)]}
  "tickets(KeepFor・終端 done)の行を開けて open-seconds の後に閉じ、閉じた刻から read-offsets の各秒で読む。答え = 各読みで行が在ったか。"
  (<- opened (PutRow "tickets" #("g" "t1") {"group" "g" "id" "t1" "state" "open"} (ExpectAbsent)))
  (<- (Delay open-seconds))
  (<- (PutRow "tickets" #("g" "t1") {"group" "g" "id" "t1" "state" "done"} (ExpectVersion opened.version)))
  (setv seen [] waited 0)
  (for [offset read-offsets]
    (<- (Delay (- offset waited)))
    (setv waited offset)
    (<- row (ReadRow "tickets" #("g" "t1")))
    (.append seen (isinstance row Row)))
  seen)


(defn test-a-row-that-becomes-terminal-later-expires-from-its-terminal-write [monkeypatch]  ; defk にできない: pytest の fixture を受ける検
  ;; 開けた行は消え得ない(終端でない)ので刻を持たない。閉じた書きが刻を足し、閉じた刻から保持の秒を過ぎて初めて消える。
  (setv scans (count-scans monkeypatch))
  (setv got (run-on (MemoryStore LAW-SCHEMA)
                    (ticket-closed-late (* 3 TICKET-KEEP-SECONDS) #(1 (- TICKET-KEEP-SECONDS 1) (+ TICKET-KEEP-SECONDS 1)))))
  (assert (= got [True True False]) got)
  (assert (= (len scans) 1) scans))


(defn test-a-store-pickled-before-the-due-time-existed-rescans-once []  ; defk にできない: 検の入口で Program を run する
  ;; purge-due-ms を持たない古い pickle から戻した置き場は、次の操作で 1 度走査して刻を数え直す。
  (setv store (MemoryStore SCHEMA))
  (setv state (.__getstate__ store))
  (del (get state "purge_due_ms"))
  (setv restored (.__new__ MemoryStore MemoryStore))
  (.__setstate__ restored state)
  (assert (= restored.purge-due-ms 0))
  (run-on restored (pulses-then-wait 1 1))
  (assert (is-not restored.purge-due-ms 0) restored.purge-due-ms))


;; --- 期限の索引(2026-09-29・agora-redesign #907)------------------------------------------------------------------------
;; 刈りは期限の索引(期限の刻で並ぶ heap)から期限の来た項だけを取り出す — 1 回の刈りの費用は行と出来事の数に比例しない。
;; 出自 = 手番の模擬の筋書き 1 つ(置く係の入れ替えが期限まで Ready にならない)が CPU 5.6 秒: 刈り 731 回がそれぞれ全部の出来事と
;; 期限つきの表の全部の行を読み、組の刻の表も毎回作り直していた。

(defn count-calls [monkeypatch names]  ; defk にできない: pytest の monkeypatch で module の関数を包む(Program の外)
  "memory の module の関数 names(行と出来事 1 つずつに対して呼ぶ判定)を包み、呼ばれた回数の合計を数える箱(list の長さ)を返すため。"
  (setv calls [])
  (for [name names]
    (setv original (getattr memory name))
    (defn counted [#* args [original original] [name name]]
      (.append calls name)
      (original #* args))
    (.setattr monkeypatch memory name counted))
  calls)


(defk journal-then-rolling-pairs [journal-count pair-count]
  {:pre [(: journal-count int) (: pair-count int)] :post [(: % tuple)]}
  "期限の無い列 journal へ journal-count 個を積み、期限つきの組の列 pairs と期限つきの表 tickets へ 1 秒おきに pair-count 回ずつ積む
   (保持 60 秒 — 60 回目から先は積むたびに前の組と行が期限を迎え、毎回刈りが走る)。答え = #(残った pairs の数 残った tickets の数)。"
  (for [i (range journal-count)]
    (<- (AppendEvent "journal" (.format "j{}" i) {"n" i})))
  (for [i (range pair-count)]
    (<- (AppendEvent "pairs" (.format "ask:p{}" i) {"n" i}))
    (<- (PutRow "tickets" #("g" (str i)) {"group" "g" "id" (str i) "state" "done"} (ExpectAbsent)))
    (<- (Delay 1)))
  (<- pairs (ReadEvents "pairs" :limit (+ pair-count 1)))
  (<- tickets (ListRows "tickets" :limit (+ pair-count 1)))
  #((len pairs.items) (len tickets.rows)))


(defn test-the-cost-of-a-purge-does-not-grow-with-the-events-in-the-store [monkeypatch]  ; defk にできない: pytest の fixture を受ける検
  ;; 反例: 期限の無い列に出来事が 0 個の置き場と 3000 個の置き場で、同じ 200 回の刈り(組と行が 1 秒ごとに期限を迎える)が
  ;; 行と出来事 1 つずつの判定(組の名・終端か)を呼ぶ回数は同じ — 前の形は刈りのたびに全部の出来事の組の名を引き直していた
  ;; (3000 個の置き場で 1 刈り 3000 回以上)。
  (setv calls (count-calls monkeypatch #("retention_group_of" "hyx_terminal_rowXquestion_markX")))
  (setv small-store (MemoryStore LAW-SCHEMA) big-store (MemoryStore LAW-SCHEMA))
  (setv small (run-on small-store (journal-then-rolling-pairs 0 260)))
  (setv small-calls (len calls))
  (.clear calls)
  (setv big (run-on big-store (journal-then-rolling-pairs 3000 260)))
  (setv big-calls (len calls))
  ;; 積んだ刻から保持の秒(60 秒)を過ぎた物は消え、読んだ刻(最後に積んでから 1 秒後)より前の 59 秒に積んだ物だけが残る。
  (assert (= small big #((- PAIR-KEEP-SECONDS 1) (- TICKET-KEEP-SECONDS 1))) #(small big))
  (assert (= small-calls big-calls) #(small-calls big-calls))
  ;; 判定は書き 1 つにつき定数回(組の名 1 回・終端か 数回)— 刈りの回数 × 置き場の大きさにならない。
  (assert (<= big-calls (* 4 260)) big-calls)
  (assert (= (len big-store.events) (+ 3000 (- PAIR-KEEP-SECONDS 1))) (len big-store.events))
  (assert (= (len big-store.groups) (- PAIR-KEEP-SECONDS 1)) (len big-store.groups)))


(defk group-extended-then-read [offsets]
  {:pre [(: offsets tuple)] :post [(: % list)]}
  "組 x の ask を 0 秒・done を 50 秒に積み(組の最後の刻が 50 秒へ延びる)、各 offsets 秒で pairs を読む。答え = 各読みの冪等キーの列。"
  (<- (AppendEvent "pairs" "ask:x" {"n" 1}))
  (<- (Delay 50))
  (<- (AppendEvent "pairs" "done:x" {"n" 2}))
  (val seen [])
  (var waited 50)
  (for [offset offsets]
    (<- (Delay (- offset waited)))
    (:= waited offset)
    (<- read (ReadEvents "pairs"))
    (.append seen (lfor event read.items event.idempotency-key)))
  seen)


(defn test-a-group-whose-last-event-moves-later-is-not-purged-early []  ; defk にできない: 検の入口で Program を run する
  ;; 反例: 組の最初の出来事の期限(60 秒)は索引の先頭に残るが、組の最後の刻が 50 秒へ動いたので読み捨てる — 61 秒ではまだ 2 つとも残り、
  ;; 組の最後の出来事から保持の秒を過ぎた 111 秒に組ごと消える。
  (setv store (MemoryStore LAW-SCHEMA))
  (setv got (run-on store (group-extended-then-read #(61 109 111))))
  (assert (= got [["ask:x" "done:x"] ["ask:x" "done:x"] []]) got)
  (assert (= store.groups {}) store.groups)
  (assert (is store.purge-due-ms None) store.purge-due-ms))


(defk tickets-closed-out-of-order []
  {:pre [] :post [(: % None)]}
  "tickets の行 t2 を 0 秒・t1 を 1 秒に終端で書き、両方の期限が過ぎた刻に 1 度だけ読む(1 回の刈りで 2 行が消える)。"
  (<- (PutRow "tickets" #("g" "t2") {"group" "g" "id" "t2" "state" "done"} (ExpectAbsent)))
  (<- (Delay 1))
  (<- (PutRow "tickets" #("g" "t1") {"group" "g" "id" "t1" "state" "done"} (ExpectAbsent)))
  (<- (Delay (+ TICKET-KEEP-SECONDS 1)))
  (<- (ReadRow "tickets" #("g" "t1")))
  None)


(defn test-rows-removed-in-one-purge-are-listed-in-key-order []  ; defk にできない: 検の入口で Program を run する
  ;; 1 回の刈りで消えた行の RowRemoved は、期限の順(t2 が先)ではなく表の名・鍵の順(t1 が先)に続きの番号で積む(前の全部の走査と
  ;; 同じ順 — 変更の列を読む使い手の答えを変えない)。
  (setv store (MemoryStore LAW-SCHEMA))
  (run-on store (tickets-closed-out-of-order))
  (setv removed (lfor change store.changes :if (isinstance change RowRemoved) change))
  (assert (= (lfor change removed change.key) [#("g" "t1") #("g" "t2")]) removed)
  (assert (= (lfor change removed change.sequence) [3 4]) removed)
  (assert (= (get store.rows "tickets") {}) store.rows))
