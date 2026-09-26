;; 反例: 同じ MemoryStore を 2 つの thread が使う(書き手の thread と、実況の読みの thread)— この系の Python は GIL の無い
;; free-threaded なので、置き場の読み書きが錠で 1 つずつになっていないと、期限つきの列の刈り(purge-expired の列の作り直し)の間に
;; 他方が積んだ出来事が消え、行の番号(store.head)の採りが重なる。積んだ出来事と行の変更が 1 つも欠けず、番号が重ならないことを確かめる。
;; 出自 = agora-redesign #741(agora-controllers の test_turn_tail が 3 回に 1 回、30 行のうち 1 行を失った)。
(require doeff-hy.macros [deftest defk <- val var])
(import dataclasses)
(import threading)
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [SimClock sim-time-handler])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.values [StreamDecl KeepFor ExpectAbsent])
(import doeff_records.effects [AppendEvent ReadEvents PutRow])
(import doeff_records.memory [MemoryStore memory-records-handler])
(import doeff_records.laws [LAW-SCHEMA MAKER])

;; 直す前の code では、この大きさで 5 回走らせて 5 回とも赤になった(出来事 400 個のうち 168〜219 個だけ残る・変更の番号が重なる)。
(val EVENT-COUNT 400)
(val ROW-COUNT 1000)
(val ROUNDS 5)

;; 期限つきの列を 1 つ足した宣言 — 列に KeepFor が 1 つでも在ると、どの操作の前の刈りも出来事の列を走査する。
(val THREAD-SCHEMA
  (dataclasses.replace LAW-SCHEMA
    :streams (FrozenMap {"journal" (get LAW-SCHEMA.streams "journal")
                         "pulses" (StreamDecl :name "pulses" :writers #(MAKER) :retention (KeepFor 60))})))


(defk append-journal [count]
  {:pre [(: count int)] :post [(: % list)]}
  "journal へ count 個を積み、答えの番号を返す。"
  (val sequences [])
  (for [i (range count)]
    (<- appended (AppendEvent "journal" (.format "j{}" i) {"n" i}))
    (.append sequences appended.sequence))
  sequences)


(defk read-while [running stream]
  {:pre [(: running threading.Event) (: stream str)] :post [(: % int)]}
  "running が立っている間、stream を読み続ける(実況の読みの thread の顔)。答え = 読んだ回数。"
  (var reads 0)
  (while (.is-set running)
    (<- (ReadEvents stream))
    (:= reads (+ reads 1)))
  reads)


(defk put-parts [prefix count]
  {:pre [(: prefix str) (: count int)] :post [(: % list)]}
  "parts に count 行を書き、答えの版を返す。"
  (val versions [])
  (for [i (range count)]
    (<- written (PutRow "parts" #((.format "{}{}" prefix i)) {"id" (.format "{}{}" prefix i) "label" "x"} (ExpectAbsent)))
    (.append versions written.version))
  versions)


(defn run-on [store program]  ; defk にできない: 検の composition root — thread ごとに run で Program を走らせる入口
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (memory-records-handler store MAKER)] program))))


(defn race [store writer-program reader-program]  ; defk にできない: thread を起こして待つ検の足場(Program の外)
  "書き手と読み手を別の thread で同時に回す。答え = 書き手の答え(どちらかの thread が失敗したらその例外を上げる)。"
  (setv running (threading.Event)
        answers {}
        failures []
        start (threading.Barrier 2))
  (.set running)
  (defn write []  ; defk にできない: threading.Thread の target に渡す callback
    (.wait start)
    (try
      (setv (get answers "writer") (run-on store writer-program))
      (except [error Exception] (.append failures error))
      (finally (.clear running))))
  (defn read []  ; defk にできない: threading.Thread の target に渡す callback
    (.wait start)
    (try
      (setv (get answers "reader") (run-on store (reader-program running)))
      (except [error Exception] (.append failures error))))
  (setv threads [(threading.Thread :target write) (threading.Thread :target read)])
  (for [t threads] (.start t))
  (for [t threads] (.join t))
  (when failures (raise (get failures 0)))
  (get answers "writer"))


(deftest test-events-appended-while-another-thread-reads-are-kept
  (for [_ (range ROUNDS)]
    (val store (MemoryStore THREAD-SCHEMA))
    (val sequences (race store (append-journal EVENT-COUNT) (fn [running] (read-while running "pulses"))))
    (assert (= sequences (list (range 1 (+ EVENT-COUNT 1)))) "積んだ番号は 1 から順に重ならない")
    (val events (run-on store (ReadEvents "journal" :limit (* 2 EVENT-COUNT))))
    (assert (= (lfor e events.items e.sequence) sequences)
            (.format "積んだ出来事が消えた: {} 個のうち {} 個だけ残る" EVENT-COUNT (len events.items)))))


(deftest test-rows-written-from-two-threads-take-distinct-change-numbers
  (for [_ (range ROUNDS)]
    (val store (MemoryStore THREAD-SCHEMA))
    (val versions (race store (put-parts "a" ROW-COUNT) (fn [running] (put-parts "b" ROW-COUNT))))
    (assert (= versions (* [1] ROW-COUNT)))
    (val numbers (lfor change store.changes change.sequence))
    (assert (= (len store.changes) (* 2 ROW-COUNT)) (.format "変更が欠けた: {} 個" (len store.changes)))
    (assert (= (sorted numbers) (list (range 1 (+ (* 2 ROW-COUNT) 1)))) "変更の番号が重なった")
    (assert (= store.head (* 2 ROW-COUNT)))))
