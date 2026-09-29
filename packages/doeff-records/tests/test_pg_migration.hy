;; PostgreSQL の置き場の表の用意(移行)は process ごとに 1 度・移行の錠の中で。
;; 性質: 同じ置き場を別の接続(別の process の代役)から同時に用意しても UniqueViolation が出ない・同時の要求は表を用意し直さない
;; (移行の文が流れるのは prepare-records-store の 1 度だけ)。
;; 反例: 移行の錠を取らない答え手では、同時の用意が CREATE … IF NOT EXISTS の競り合いで UniqueViolation(SQLSTATE 23505)になる
;; (「出ない」の判定が赤になる形 — 判定が何も確かめずに緑になる形を外す)。
;; env DOEFF_RECORDS_TEST_PG_DSN が無ければ skip。
(require doeff-hy.macros [deftest val])
(import os)
(import threading)
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.postgres_sql [postgres-sql-handler])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records.values [Missing])
(import doeff_records.effects [ReadRow])
(import doeff_records.laws [LAW-SCHEMA MAKER])
(import doeff_records.pg [pg-records-handler drop-records-tables prepare-records-store RecordsSqlFailed DEFAULT-POLL-SECONDS])
(import doeff_records.pg_sql [schema-statements])
(import tests.interpreters [PG-DSN-VARIABLE DATABASE ORIGIN-HOST postgres-connections fresh-prefix run-sql])
(import tests.sql_probes [QueryProbe StatementCounts probe-sql-handler])

(val PG-DSN (.get os.environ PG-DSN-VARIABLE))
(val RACERS 4)
(val ROUNDS 10)


(defn race-prepares [#^ str prefix * keep-lock]  ; defk にできない: RACERS 本の thread でそれぞれ別の run を回す入口
  "RACERS 本の接続から同じ置き場を同時に用意し、上がった失敗の SQLSTATE(他の例外は型の名)の列を返す。"
  (setv connections (postgres-connections RACERS)
        start (threading.Barrier RACERS)
        raised [])
  (defn attempt []  ; defk にできない: thread の target
    (.wait start)
    (try
      (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock))
                                      (probe-sql-handler connections DATABASE (QueryProbe (StatementCounts) :keep-lock keep-lock))]
                                     (prepare-records-store DATABASE LAW-SCHEMA prefix))))
      (except [error RecordsSqlFailed]
        (.append raised error.sqlstate))
      (except [error Exception]
        (.append raised (. (type error) __name__)))))
  (setv threads (lfor _ (range RACERS) (threading.Thread :target attempt)))
  (for [t threads] (.start t))
  (for [t threads] (.join t))
  (.close connections)
  raised)


(defn cleanup [#^ str prefix]  ; defk にできない: 検の後片付けで別の run を回す入口
  "検の置き場の表を消す。"
  (setv connections (postgres-connections 1))
  (run-sql connections (drop-records-tables (run-sql connections (prepare-records-store DATABASE LAW-SCHEMA prefix))))
  (.close connections))


(deftest test-concurrent-prepares-of-one-store-raise-no-unique-violation
  {:skip-if (not PG-DSN) :skip-reason "DOEFF_RECORDS_TEST_PG_DSN が無い(PostgreSQL の検は走っていない)"}
  ;; 別の process(replicas > 1・入れ替えの重なり)の代役: 新しい置き場を RACERS 本の接続が同時に用意する。
  (for [prefix (lfor _ (range ROUNDS) (fresh-prefix))]
    (try
      (assert (= (race-prepares prefix :keep-lock True) []) prefix)
      (finally (cleanup prefix)))))


(deftest test-without-the-migrate-lock-concurrent-prepares-collide
  {:skip-if (not PG-DSN) :skip-reason "DOEFF_RECORDS_TEST_PG_DSN が無い(PostgreSQL の検は走っていない)"}
  ;; 反例: 移行の錠を外すと、同じ競り合いで UniqueViolation(23505)が出る(IF NOT EXISTS だけでは防げない)。
  (val seen [])
  (for [prefix (lfor _ (range ROUNDS) (fresh-prefix))]
    (try
      (.extend seen (race-prepares prefix :keep-lock False))
      (finally (cleanup prefix))))
  (assert (in "23505" seen) (repr seen)))


(deftest test-concurrent-requests-do-not-migrate-again
  {:skip-if (not PG-DSN) :skip-reason "DOEFF_RECORDS_TEST_PG_DSN が無い(PostgreSQL の検は走っていない)"}
  ;; 起動直後の同時の要求の代役: 表を 1 度用意した後、RACERS 本の要求が同時に読む。移行の文は最初の 1 度だけ流れる。
  (val prefix (fresh-prefix))
  (val counts (StatementCounts))
  (val connections (postgres-connections RACERS))
  (val counting (probe-sql-handler connections DATABASE (QueryProbe counts)))
  (val store (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) counting]
                                            (prepare-records-store DATABASE LAW-SCHEMA prefix)))))
  (val creates-after-prepare counts.creates)
  (val start (threading.Barrier RACERS))
  (val answers [])
  (defn request []  ; defk にできない: thread の target
    (.wait start)
    (.append answers (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) counting
                                                     (pg-records-handler store MAKER ORIGIN-HOST DEFAULT-POLL-SECONDS)]
                                                    (ReadRow "parts" #("p1")))))))
  (try
    (val threads (lfor _ (range RACERS) (threading.Thread :target request)))
    (for [t threads] (.start t))
    (for [t threads] (.join t))
    (assert (= answers (* [(Missing)] RACERS)) (repr answers))
    (assert (= creates-after-prepare
               (len (lfor s (run (schema-statements prefix LAW-SCHEMA)) :if (.startswith (.lstrip s.text) "CREATE") s)))
            creates-after-prepare)
    (assert (= counts.creates creates-after-prepare) (repr #(creates-after-prepare counts.creates)))
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))
