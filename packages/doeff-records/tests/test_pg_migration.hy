;; PostgreSQL の置き場の表の用意(移行)は process ごとに 1 度・移行の lock の中で。
;; 性質: 同じ置き場を別の接続(別の process の代役)から同時に用意しても UniqueViolation が出ない・pool を通した同時の要求は
;; 表を用意し直さない(移行の文が流れるのは prepare-records-store の 1 度だけ)。
;; 反例: 移行の lock を流さない接続では、同時の用意が CREATE … IF NOT EXISTS の競り合いで UniqueViolation になる
;; (「出ない」の判定が赤になる形 — 判定が何も確かめずに緑になる形を外す)。
;; env DOEFF_RECORDS_TEST_PG_DSN が無ければ skip。
(require doeff-hy.macros [deftest val])
(import os)
(import threading)
(import uuid)
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records.values [Missing])
(import doeff_records.effects [ReadRow])
(import doeff_records.laws [LAW-SCHEMA MAKER])
(import doeff_records.pg [pg-records-handler drop-records-tables prepare-records-store PgRecordsHost])
(import doeff_records.pg_pool [PgHostPool])
(import doeff_records.pg_sql [schema-statements])
(import tests.interpreters [PG-DSN-VARIABLE open-postgres pg-errors])

(val PG-DSN (.get os.environ PG-DSN-VARIABLE))
(val RACERS 4)
(val ROUNDS 10)


(defn fresh-prefix [] (+ "t" (cut (. (uuid.uuid4) hex) 12) "_"))


(defclass CountingConnection []
  "検の代役の接続: 本物の psycopg の接続に流しつつ、流れた文を数える(counts = 全部の接続で共有する数え)。
   lockless = True なら移行の lock の文を流さない(反例の接続)。"
  (defn __init__ [self connection counts * [lockless False]]
    (setv self.inner connection self.counts counts self.lockless lockless))
  (defn [property] autocommit [self] self.inner.autocommit)
  (defn [property] closed [self] self.inner.closed)
  (defn [property] broken [self] self.inner.broken)
  (defn transaction [self] (.transaction self.inner))
  (defn close [self] (.close self.inner))
  (defn execute [self query [params #()]]
    (.record self.counts query)
    (if (and self.lockless (in "records-migrate" (str params)))
        None
        (.execute self.inner query params))))


(defclass StatementCounts []
  "流れた文の数え(thread の間で共有): creates = CREATE の文の数。"
  (defn __init__ [self]
    (setv self.lock (threading.Lock) self.creates 0))
  (defn record [self #^ str query]
    (with [self.lock]
      (when (.startswith (.lstrip query) "CREATE") (+= self.creates 1)))))


(defn race-prepares [#^ str prefix * lockless]
  "RACERS 本の接続から同じ置き場を同時に用意し、上がった例外の型の名の列を返す。"
  (setv start (threading.Barrier RACERS) raised [] connections (lfor _ (range RACERS) (open-postgres)))
  (defn attempt [connection]
    (.wait start)
    (try
      (prepare-records-store (CountingConnection connection (StatementCounts) :lockless lockless) LAW-SCHEMA prefix)
      (except [error Exception]
        (.append raised (. (type error) __name__)))))
  (setv threads (lfor c connections (threading.Thread :target attempt :args #(c))))
  (for [t threads] (.start t))
  (for [t threads] (.join t))
  (for [c connections] (.close c))
  raised)


(defn cleanup [#^ str prefix]
  (setv connection (open-postgres))
  (drop-records-tables (PgRecordsHost connection (prepare-records-store connection LAW-SCHEMA prefix) :unreachable-errors (pg-errors)))
  (.close connection))


(deftest test-concurrent-prepares-of-one-store-raise-no-unique-violation
  {:skip-if (not PG-DSN) :skip-reason "DOEFF_RECORDS_TEST_PG_DSN が無い(PostgreSQL の検は走っていない)"}
  ;; 別の process(replicas > 1・入れ替えの重なり)の代役: 新しい置き場を RACERS 本の接続が同時に用意する。
  (for [prefix (lfor _ (range ROUNDS) (fresh-prefix))]
    (try
      (assert (= (race-prepares prefix :lockless False) []) prefix)
      (finally (cleanup prefix)))))


(deftest test-without-the-migrate-lock-concurrent-prepares-collide
  {:skip-if (not PG-DSN) :skip-reason "DOEFF_RECORDS_TEST_PG_DSN が無い(PostgreSQL の検は走っていない)"}
  ;; 反例: 移行の lock を外すと、同じ競り合いで UniqueViolation が出る(IF NOT EXISTS だけでは防げない)。
  (val seen [])
  (for [prefix (lfor _ (range ROUNDS) (fresh-prefix))]
    (try
      (.extend seen (race-prepares prefix :lockless True))
      (finally (cleanup prefix))))
  (assert (in "UniqueViolation" seen) (repr seen)))


(deftest test-concurrent-leases-from-the-pool-do-not-migrate-again
  {:skip-if (not PG-DSN) :skip-reason "DOEFF_RECORDS_TEST_PG_DSN が無い(PostgreSQL の検は走っていない)"}
  ;; 起動直後の同時の要求の代役: 表を 1 度用意した後、pool から RACERS 本を同時に借りて読む。移行の文は最初の 1 度だけ流れる。
  (val prefix (fresh-prefix))
  (val counts (StatementCounts))
  (val first (CountingConnection (open-postgres) counts))
  (val store (prepare-records-store first LAW-SCHEMA prefix))
  (val creates-after-prepare counts.creates)
  (val pool (PgHostPool (fn [] (CountingConnection (open-postgres) counts)) store :unreachable-errors (pg-errors) :size RACERS))
  (val start (threading.Barrier RACERS))
  (val answers [])
  (defn request []
    (with [host (.lease pool)]
      (.wait start)
      (.append answers (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (pg-records-handler host MAKER)]
                                                      (ReadRow "parts" #("p1"))))))))
  (try
    (val threads (lfor _ (range RACERS) (threading.Thread :target request)))
    (for [t threads] (.start t))
    (for [t threads] (.join t))
    (assert (= answers (* [(Missing)] RACERS)) (repr answers))
    (assert (= (len pool.opened) RACERS) (repr pool.opened))
    (assert (= creates-after-prepare
               (len (lfor s (schema-statements prefix LAW-SCHEMA) :if (.startswith (.lstrip s.text) "CREATE") s)))
            creates-after-prepare)
    (assert (= counts.creates creates-after-prepare) (repr #(creates-after-prepare counts.creates)))
    (finally
      (.close pool)
      (drop-records-tables (PgRecordsHost first store :unreachable-errors (pg-errors)))
      (.close first))))
