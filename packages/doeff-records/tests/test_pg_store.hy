;; PostgreSQL の置き場だけの性質: 別の接続からの同時の ExpectAbsent は 1 つだけ通る・接続を開き直しても行と番号が続く・
;; 接続の失敗は Unreachable の答え・自動 commit でない接続は組み立てで断る・PutRows の書きの途中で接続が落ちても 1 行も残らない。
;; env DOEFF_RECORDS_TEST_PG_DSN が無ければ skip。
;; 反例: 置き場の書きの lock を流さない host では、同時の ExpectAbsent が 2 つとも通る(「1 つだけ通る」の判定が赤になる)。
(require doeff-hy.macros [deftest val])
(import os)
(import threading)
(import uuid)
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records.values [ExpectAbsent Written Conflict Row Unreachable Missing WrittenRows])
(import doeff_records.effects [PutRow PutRows RowWrite ReadRow ListRows])
(import doeff_records.laws [LAW-SCHEMA MAKER])
(import doeff_records.pg [PgRecordsHost pg-records-handler drop-records-tables])
(import tests.interpreters [PG-DSN-VARIABLE open-postgres pg-errors])

(setv PG-DSN (.get os.environ PG-DSN-VARIABLE))


(defn fresh-prefix [] (+ "t" (cut (. (uuid.uuid4) hex) 12) "_"))

(defn #^ bool admitted-exactly-one? [#^ list answers]
  "同時の ExpectAbsent 2 つの答えが「1 つだけ通り、1 つは衝突」か(lock の検と反例が同じ判定を使う)。"
  (= (sorted (lfor a answers (. (type a) __name__))) ["Conflict" "Written"]))


(defclass LocklessHost [PgRecordsHost]
  "反例の host: 置き場の書きの lock を流さず、行の読み(FOR UPDATE)の後で 2 本の書きを揃える(両方が「行が無い」を読んでから書く)。"
  (defn __init__ [self connection schema barrier * prefix]
    (setv self.barrier barrier)
    (.__init__ (super) connection schema :unreachable-errors (pg-errors) :prefix prefix))
  (defn execute [self statement]
    (when (.startswith statement.text "SELECT pg_advisory_xact_lock")
      (return None))
    (setv cursor (.execute (super) statement))
    (when (.endswith (.rstrip statement.text) "FOR UPDATE")
      (.wait self.barrier))
    cursor))


(defn race-absent-writes [hosts #^ str key]
  "2 つの host から同じ鍵へ同時に ExpectAbsent を撃ち、答えを返す。"
  (setv start (threading.Barrier 2) found [])
  (defn attempt [host label]
    (.wait start)
    (.append found (run-on host (PutRow "parts" #(key) {"label" label} (ExpectAbsent)))))
  (setv threads (lfor #(i host) (enumerate hosts) (threading.Thread :target attempt :args #(host (str i)))))
  (for [t threads] (.start t))
  (for [t threads] (.join t))
  found)


(defn run-on [host program]
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (pg-records-handler host MAKER)] program))))


(deftest test-concurrent-absent-writes-from-two-connections-admit-exactly-one
  {:skip-if (not PG-DSN) :skip-reason "DOEFF_RECORDS_TEST_PG_DSN が無い(PostgreSQL の検は走っていない)"}
  ;; 行が無い時の期待は、無い行に鍵が掛からない(READ COMMITTED に述語の鍵は無い)ので、置き場の lock が無いと 2 つとも通る。
  (setv prefix (fresh-prefix)
        connections [(open-postgres) (open-postgres)]
        hosts (lfor c connections (PgRecordsHost c LAW-SCHEMA :unreachable-errors (pg-errors) :prefix prefix))
        answers {})
  (try
    (for [round (range 20)]
      (setv found (race-absent-writes hosts (.format "race-{}" round)))
      (setv (get answers round) found)
      (assert (admitted-exactly-one? found) (repr found)))
    (finally
      (drop-records-tables (get hosts 0))
      (for [c connections] (.close c)))))


(deftest test-without-the-store-lock-two-absent-writes-both-pass
  {:skip-if (not PG-DSN) :skip-reason "DOEFF_RECORDS_TEST_PG_DSN が無い(PostgreSQL の検は走っていない)"}
  ;; 反例: 上の検の判定(admitted-exactly-one?)が、lock を外した host では赤になる — 判定が何も確かめずに緑になる形を外す。
  (setv prefix (fresh-prefix)
        barrier (threading.Barrier 2 :timeout 10)
        connections [(open-postgres) (open-postgres)]
        hosts (lfor c connections (LocklessHost c LAW-SCHEMA barrier :prefix prefix)))
  (try
    (setv found (race-absent-writes hosts "race-lockless"))
    (assert (= (lfor a found (. (type a) __name__)) ["Written" "Written"]) (repr found))
    (assert (not (admitted-exactly-one? found)) (repr found))
    (finally
      (drop-records-tables (get hosts 0))
      (for [c connections] (.close c)))))


(deftest test-rows-and-numbers-survive-reconnect
  {:skip-if (not PG-DSN) :skip-reason "DOEFF_RECORDS_TEST_PG_DSN が無い(PostgreSQL の検は走っていない)"}
  (setv prefix (fresh-prefix) first (open-postgres))
  (setv host (PgRecordsHost first LAW-SCHEMA :unreachable-errors (pg-errors) :prefix prefix))
  (setv written (run-on host (PutRow "parts" #("p1") {"label" "a"} (ExpectAbsent))))
  (setv page (run-on host (ListRows "parts")))
  (.close first)
  (setv second (open-postgres))
  ;; 2 度目の host は同じ表をもう一度用意する(IF NOT EXISTS / ON CONFLICT DO NOTHING で何も壊さない — 版と番号が続く)。
  (setv again (PgRecordsHost second LAW-SCHEMA :unreachable-errors (pg-errors) :prefix prefix))
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
  (setv host (PgRecordsHost connection LAW-SCHEMA :unreachable-errors (pg-errors) :prefix prefix))
  (drop-records-tables host)
  (.close connection)
  (setv answer (run-on host (ReadRow "parts" #("p1"))))
  (assert (isinstance answer Unreachable) (repr answer))
  (setv put (run-on host (PutRow "parts" #("p1") {"label" "a"} (ExpectAbsent))))
  (assert (isinstance put Unreachable) (repr put)))


(defclass FailingMidBatchHost [PgRecordsHost]
  "書きの途中で接続が落ちる host: 行の書き(state_rows への INSERT)の fail-at 本目で接続の失敗の例外を上げる
   (PutRows の束が 1 transaction の中で戻ることの検のため)。"
  (defn __init__ [self connection schema * prefix fail-at]
    (setv self.fail-at fail-at self.upserts 0)
    (.__init__ (super) connection schema :unreachable-errors (pg-errors) :prefix prefix))
  (defn execute [self statement]
    (when (.startswith (.lstrip statement.text) (.format "INSERT INTO {}state_rows" self.prefix))
      (+= self.upserts 1)
      (when (= self.upserts self.fail-at)
        (raise ((get (pg-errors) 0) "検の代役: 書きの途中で接続が落ちた"))))
    (.execute (super) statement)))


(deftest test-put-rows-that-fails-mid-write-leaves-no-row-and-no-change
  {:skip-if (not PG-DSN) :skip-reason "DOEFF_RECORDS_TEST_PG_DSN が無い(PostgreSQL の検は走っていない)"}
  ;; 束の 2 行目の書きで接続が落ちる: 答えは Unreachable で、1 行目の書きも変更の列の 1 つも残らない(transaction ごと戻る)。
  (val prefix (fresh-prefix))
  (val connection (open-postgres))
  (val host (FailingMidBatchHost connection LAW-SCHEMA :prefix prefix :fail-at 2))
  (val writes #((RowWrite "parts" #("p1") {"label" "a"} (ExpectAbsent)) (RowWrite "parts" #("p2") {"label" "b"} (ExpectAbsent))))
  (try
    (val before (run-on host (ListRows "parts")))
    (val answer (run-on host (PutRows writes)))
    (assert (isinstance answer Unreachable) (repr answer))
    (assert (= (run-on host (ReadRow "parts" #("p1"))) (Missing)) "落ちる前に書いた 1 行目が残った")
    (val after (run-on host (ListRows "parts")))
    (assert (and (= after.rows #()) (= after.sequence before.sequence)) (repr #(before after)))
    ;; 同じ束を撃ち直すと通る(期待つきの書きは撃ち直してよい)。
    (assert (isinstance (run-on host (PutRows writes)) WrittenRows))
    (finally
      (drop-records-tables host)
      (.close connection))))


(deftest test-a-connection-without-autocommit-is-refused
  {:skip-if (not PG-DSN) :skip-reason "DOEFF_RECORDS_TEST_PG_DSN が無い(PostgreSQL の検は走っていない)"}
  (import psycopg)
  (setv connection (psycopg.connect (get os.environ PG-DSN-VARIABLE)))
  (try
    (try
      (PgRecordsHost connection LAW-SCHEMA :unreachable-errors (pg-errors) :prefix (fresh-prefix))
      (assert False "自動 commit でない接続を受けた")
      (except [ValueError] None))
    (finally (.close connection))))
