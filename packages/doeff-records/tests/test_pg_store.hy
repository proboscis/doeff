;; PostgreSQL の置き場だけの性質: 別の接続からの同時の ExpectAbsent は 1 つだけ通る・接続を開き直しても行と番号が続く・
;; 置き場に届かなければ Unreachable の答え・PutRows の書きの途中で接続が落ちても 1 行も残らない。
;; SQL の effect の答え手は doeff の postgres-sql-handler(反例と故障は driver の手前の代役 tests/sql_probes.hy)。
;; env DOEFF_RECORDS_TEST_PG_DSN が無ければ conftest が使い捨ての PostgreSQL を立てて置く(#2830)。立てられなければ理由を名指して skip。
;; 反例: 置き場の書きの錠を取らない答え手では、同時の ExpectAbsent が 2 つとも通る(「1 つだけ通る」の判定が赤になる)。
(require doeff-hy.macros [deftest val var])
(import threading)
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.postgres_sql [PostgresConnections PostgresDatabase postgres-sql-handler])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records.values [ExpectAbsent Written Conflict Row Unreachable Missing WrittenRows])
(import doeff_records.effects [PutRow PutRows RowWrite ReadRow ListRows])
(import doeff_records.laws [LAW-SCHEMA MAKER])
(import doeff_records.pg [PreparedStore pg-records-handler drop-records-tables DEFAULT-POLL-SECONDS])
(import tests.interpreters [session-dsn PG-DSN-VARIABLE pg-skip-reason DATABASE ORIGIN-HOST postgres-connections fresh-prefix run-sql prepared-store])
(import tests.sql_probes [QueryProbe StatementCounts probe-sql-handler])

(val PG-DSN (session-dsn PG-DSN-VARIABLE))
;; env が無ければ conftest が使い捨ての PostgreSQL を立てて置く(#2830)— 無いのは立てられなかった時で、その理由を名指す。
(val PG-SKIP-REASON (pg-skip-reason))


(defn admitted-exactly-one? [#^ list answers]
  "同時の ExpectAbsent 2 つの答えが「1 つだけ通り、1 つは衝突」か(錠の検と反例が同じ判定を使う)。"
  (= (sorted (lfor a answers (. (type a) __name__))) ["Conflict" "Written"]))


(defn run-on [answerer store program]
  "答え手 answerer(SQL の effect の答え手)の上で、置き場 store の書き手 maker として program を 1 回走らせる。"
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) answerer
                                  (pg-records-handler store MAKER ORIGIN-HOST DEFAULT-POLL-SECONDS)]
                                 program))))


(defn race-absent-writes [answerers store #^ str key]
  "2 つの答え手(別々の接続)から同じ鍵へ同時に ExpectAbsent を撃ち、答えを返す。"
  (setv start (threading.Barrier 2) found [])
  (defn attempt [answerer label]
    (.wait start)
    (.append found (run-on answerer store (PutRow "parts" #(key) {"label" label} (ExpectAbsent)))))
  (setv threads (lfor #(i answerer) (enumerate answerers) (threading.Thread :target attempt :args #(answerer (str i)))))
  (for [t threads] (.start t))
  (for [t threads] (.join t))
  found)


(deftest test-concurrent-absent-writes-from-two-connections-admit-exactly-one
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; 行が無い時の期待は、無い行に鍵が掛からない(READ COMMITTED に述語の鍵は無い)ので、置き場の錠が無いと 2 つとも通る。
  (val connections (postgres-connections 2))
  (val store (prepared-store connections (fresh-prefix)))
  (try
    (for [round (range 20)]
      (val found (race-absent-writes [(postgres-sql-handler connections) (postgres-sql-handler connections)] store
                                     (.format "race-{}" round)))
      (assert (admitted-exactly-one? found) (repr found)))
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))


(deftest test-without-the-store-lock-two-absent-writes-both-pass
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; 反例: 上の検の判定(admitted-exactly-one?)が、錠を取らない答え手では赤になる — 判定が何も確かめずに緑になる形を外す。
  (val connections (postgres-connections 2))
  (val store (prepared-store connections (fresh-prefix)))
  (val probe (QueryProbe (StatementCounts) :keep-lock False :barrier (threading.Barrier 2 :timeout 10)))
  (try
    (val found (race-absent-writes [(probe-sql-handler connections DATABASE probe) (probe-sql-handler connections DATABASE probe)]
                                   store "race-lockless"))
    (assert (= (lfor a found (. (type a) __name__)) ["Written" "Written"]) (repr found))
    (assert (not (admitted-exactly-one? found)) (repr found))
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))


(deftest test-rows-and-numbers-survive-reconnect
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val prefix (fresh-prefix))
  (val first (postgres-connections 1))
  (val store (prepared-store first prefix))
  (val written (run-on (postgres-sql-handler first) store (PutRow "parts" #("p1") {"label" "a"} (ExpectAbsent))))
  (val page (run-on (postgres-sql-handler first) store (ListRows "parts")))
  (.close first)
  (val second (postgres-connections 1))
  ;; 2 度目の用意は同じ表をもう一度用意する(IF NOT EXISTS / ON CONFLICT DO NOTHING で何も壊さない — 版と番号が続く)。
  (val again (prepared-store second prefix))
  (try
    (assert (= (run-on (postgres-sql-handler second) again (ReadRow "parts" #("p1"))) (Row #("p1") written.value 1)))
    (val later (run-on (postgres-sql-handler second) again (ListRows "parts")))
    (assert (= #(later.epoch later.sequence) #(page.epoch page.sequence)) (repr #(page later)))
    (finally
      (run-sql second (drop-records-tables again))
      (.close second))))


(deftest test-an-unreachable-store-answers-unreachable
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; 接続できない置き場(開いていない port)— 答え手の SqlUnreachable が公開 effect の答え Unreachable になる(読みも書きも)。
  (val closed (PostgresConnections #((PostgresDatabase :name DATABASE :dsn "postgresql://nobody@127.0.0.1:1/none?connect_timeout=2"))
                                   :size 1))
  (val store (PreparedStore :database DATABASE :schema LAW-SCHEMA :prefix (fresh-prefix)))
  (val answer (run-on (postgres-sql-handler closed) store (ReadRow "parts" #("p1"))))
  (assert (isinstance answer Unreachable) (repr answer))
  (val put (run-on (postgres-sql-handler closed) store (PutRow "parts" #("p1") {"label" "a"} (ExpectAbsent))))
  (assert (isinstance put Unreachable) (repr put)))


(deftest test-put-rows-that-fails-mid-write-leaves-no-row-and-no-change
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; 束の 2 行目の書きで接続が落ちる: 答えは Unreachable で、1 行目の書きも変更の列の 1 つも残らない(transaction ごと戻る)。
  (val connections (postgres-connections 1))
  (val store (prepared-store connections (fresh-prefix)))
  (val failing (probe-sql-handler connections DATABASE (QueryProbe (StatementCounts) :fail-at 2)))
  (val plain (postgres-sql-handler connections))
  (val writes #((RowWrite "parts" #("p1") {"label" "a"} (ExpectAbsent)) (RowWrite "parts" #("p2") {"label" "b"} (ExpectAbsent))))
  (try
    (val before (run-on plain store (ListRows "parts")))
    (val answer (run-on failing store (PutRows writes)))
    (assert (isinstance answer Unreachable) (repr answer))
    (assert (= (run-on plain store (ReadRow "parts" #("p1"))) (Missing)) "落ちる前に書いた 1 行目が残った")
    (val after (run-on plain store (ListRows "parts")))
    (assert (and (= after.rows #()) (= after.sequence before.sequence)) (repr #(before after)))
    ;; 同じ束を撃ち直すと通る(期待つきの書きは撃ち直してよい)。
    (assert (isinstance (run-on plain store (PutRows writes)) WrittenRows))
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))
