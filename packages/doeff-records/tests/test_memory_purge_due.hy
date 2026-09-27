;; memory の置き場の保持の刈り(purge-expired)は、消え得る刻(MemoryStore.purge-due-ms)より前なら行と出来事を走査しない。
;; 出自 = 2026-09-27 の保持の刈りの性能の直し: 刈りはどの操作の前にも呼ばれ、期限つきの列が 1 本でも在ると毎回すべての出来事を読み直していた —
;; 出来事 1 万を積む模擬の筋書き 1 つが 5 分を越えた(出来事の数の 2 乗)。消える物は変わらない(法 12 などの保持の法が memory と pg で守る)。
(require doeff-hy.macros [defk <- val])
(import dataclasses)
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [SimClock sim-time-handler Delay])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.values [StreamDecl KeepFor ExpectAbsent ExpectVersion Row Missing])
(import doeff_records.effects [AppendEvent ReadEvents PutRow ReadRow])
(import doeff_records.memory :as memory)
(import doeff_records.memory [MemoryStore memory-records-handler])
(import doeff_records.laws [LAW-SCHEMA MAKER TICKET-KEEP-SECONDS])

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
