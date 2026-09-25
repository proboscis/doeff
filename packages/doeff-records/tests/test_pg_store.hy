;; PostgreSQL の置き場だけの性質: 別の接続からの同時の ExpectAbsent は 1 つだけ通る・接続を開き直しても行と番号が続く・
;; 接続の失敗は Unreachable の答え・自動 commit でない接続は組み立てで断る。env DOEFF_RECORDS_TEST_PG_DSN が無ければ skip。
(require doeff-hy.macros [deftest])
(import os)
(import threading)
(import uuid)
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records.values [ExpectAbsent Written Conflict Row Unreachable])
(import doeff_records.effects [PutRow ReadRow ListRows])
(import doeff_records.laws [LAW-SCHEMA MAKER])
(import doeff_records.pg [PgRecordsHost pg-records-handler drop-records-tables])
(import tests.interpreters [PG-DSN-VARIABLE open-postgres])

(setv PG-DSN (.get os.environ PG-DSN-VARIABLE))


(defn fresh-prefix [] (+ "t" (cut (. (uuid.uuid4) hex) 12) "_"))

(defn run-on [host program]
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (pg-records-handler host MAKER)] program))))


(deftest test-concurrent-absent-writes-from-two-connections-admit-exactly-one
  {:skip-if (not PG-DSN) :skip-reason "DOEFF_RECORDS_TEST_PG_DSN が無い(PostgreSQL の検は走っていない)"}
  ;; 行が無い時の期待は、無い行に鍵が掛からない(READ COMMITTED に述語の鍵は無い)ので、置き場の lock が無いと 2 つとも通る。
  (setv prefix (fresh-prefix)
        connections [(open-postgres) (open-postgres)]
        hosts (lfor c connections (PgRecordsHost c LAW-SCHEMA :prefix prefix))
        answers {})
  (try
    (for [round (range 20)]
      (setv key #((.format "race-{}" round)) barrier (threading.Barrier 2) found [])
      (defn attempt [host label]
        (.wait barrier)
        (.append found (run-on host (PutRow "parts" key {"label" label} (ExpectAbsent)))))
      (setv threads (lfor #(i host) (enumerate hosts) (threading.Thread :target attempt :args #(host (str i)))))
      (for [t threads] (.start t))
      (for [t threads] (.join t))
      (setv (get answers round) found)
      (assert (= (sorted (lfor a found (. (type a) __name__))) ["Conflict" "Written"]) (repr found)))
    (finally
      (drop-records-tables (get hosts 0))
      (for [c connections] (.close c)))))


(deftest test-rows-and-numbers-survive-reconnect
  {:skip-if (not PG-DSN) :skip-reason "DOEFF_RECORDS_TEST_PG_DSN が無い(PostgreSQL の検は走っていない)"}
  (setv prefix (fresh-prefix) first (open-postgres))
  (setv host (PgRecordsHost first LAW-SCHEMA :prefix prefix))
  (setv written (run-on host (PutRow "parts" #("p1") {"label" "a"} (ExpectAbsent))))
  (setv page (run-on host (ListRows "parts")))
  (.close first)
  (setv second (open-postgres))
  ;; 2 度目の host は同じ表をもう一度用意する(IF NOT EXISTS / ON CONFLICT DO NOTHING で何も壊さない — 版と番号が続く)。
  (setv again (PgRecordsHost second LAW-SCHEMA :prefix prefix))
  (try
    (assert (= (run-on again (ReadRow "parts" #("p1"))) (Row #("p1") written.value 1)))
    (setv later (run-on again (ListRows "parts")))
    (assert (= #(later.epoch later.sequence) #(page.epoch page.sequence)) (repr #(page later)))
    (finally
      (drop-records-tables again)
      (.close second))))


(deftest test-a-lost-connection-answers-unreachable
  {:skip-if (not PG-DSN) :skip-reason "DOEFF_RECORDS_TEST_PG_DSN が無い(PostgreSQL の検は走っていない)"}
  (setv prefix (fresh-prefix) connection (open-postgres))
  (setv host (PgRecordsHost connection LAW-SCHEMA :prefix prefix))
  (drop-records-tables host)
  (.close connection)
  (setv answer (run-on host (ReadRow "parts" #("p1"))))
  (assert (isinstance answer Unreachable) (repr answer))
  (setv put (run-on host (PutRow "parts" #("p1") {"label" "a"} (ExpectAbsent))))
  (assert (isinstance put Unreachable) (repr put)))


(deftest test-a-connection-without-autocommit-is-refused
  {:skip-if (not PG-DSN) :skip-reason "DOEFF_RECORDS_TEST_PG_DSN が無い(PostgreSQL の検は走っていない)"}
  (import psycopg)
  (setv connection (psycopg.connect (get os.environ PG-DSN-VARIABLE)))
  (try
    (try
      (PgRecordsHost connection LAW-SCHEMA :prefix (fresh-prefix))
      (assert False "自動 commit でない接続を受けた")
      (except [ValueError] None))
    (finally (.close connection))))
