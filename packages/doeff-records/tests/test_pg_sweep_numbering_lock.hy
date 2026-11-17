;; 2026-11-17: PostgreSQL の置き場の回収(SweepExpired)が番号の錠(ROWS-UNIT — 変更の列の番号を全部の表で 1 つに並べる錠)を持つ間に、
;; 記録の service との往復を待たず、錠を持つ間に触る行が上限で縛られる事。
;; 本番の測り(pg_locks と pg_stat_activity を 0.13 秒ごとに 170 秒): 行の回収 1 回が、番号の錠を取った後も変更の列に「消えた」を積む文を
;; 1 文ずつ往復で流し、錠を持ったまま `idle in transaction` で 9 秒以上(transaction は 34.6 秒)。行の書きの冪等キーの覚えの外し
;; (EXPIRE-BATCH 個)は番号の錠の中で WAL の書きを待って 3.5 秒持った。その間、全部の表の行の書きが番号の錠を待った。
;; 性質:
;;   - 期限を過ぎた終端の行 MANY-ROWS 行(SWEEP-ROWS-BATCH を 2 回越える)の置き場で SweepExpired を 1 回撃つと全部消え、変更の列に「消えた」を
;;     積む文を流す往復はどれも番号の錠と COMMIT を同じ往復に持ち(錠を取ってから commit まで往復を待たない)、往復ごとの積む文は
;;     SWEEP-ROWS-BATCH 個まで(⌈MANY-ROWS / SWEEP-ROWS-BATCH⌉ 往復)。候補の読みは錠の外。
;;   - 覚えた刻から WRITE-KEY-MEMORY-SECONDS を過ぎた行の書きの冪等キーの覚え MANY-KEYS 個を SweepExpired 1 回で全部外し、外しの
;;     transaction は ⌈MANY-KEYS / FORGET-WRITE-KEYS-BATCH⌉ 個に分かれる。
;; 反例(前の形): 行の回収は文 1 つずつの transaction で、積む文ごとに往復 1 回(番号の錠の往復に COMMIT が無い)— 赤。覚えの外しは
;; EXPIRE-BATCH 個ずつで、MANY-KEYS 個を transaction 1 つで外した — 赤。
;; env DOEFF_RECORDS_TEST_PG_DSN が無ければ conftest が使い捨ての PostgreSQL を立てて置く(#2830)。立てられなければ理由を名指して skip。
(require doeff-hy.macros [deftest defk <- val var])
(import doeff_hy._frozen [FrozenMap])
(import doeff [run with_handlers])
(import doeff_core_effects [scheduled])
(import doeff_core_effects [SqlQuery SqlRows])
(import doeff_core_effects [postgres-sql-handler])
(import doeff_time [SimClock sim-time-handler Delay])
(import doeff_records [ExpectAbsent Written PutRow SweepExpired Swept])
(import doeff_records._laws [MAKER TICKET-KEEP-SECONDS])
(import doeff_records._values [WRITE-KEY-MEMORY-SECONDS])
(import doeff_records [pg-records-handler]
        doeff_records._pg [drop-records-tables SWEEP-ROWS-BATCH FORGET-WRITE-KEYS-BATCH])
(import doeff_records._pg_sql [numbering-lock-statement])
(import tests.interpreters [session-dsn PG-DSN-VARIABLE pg-skip-reason DATABASE ORIGIN-HOST postgres-connections fresh-prefix run-sql
                            prepared-store])
(import tests.sql_probes [QueryProbe StatementCounts probe-sql-handler effect-round-trips])

(val PG-DSN (session-dsn PG-DSN-VARIABLE))
;; env が無ければ conftest が使い捨ての PostgreSQL を立てて置く(#2830)— 無いのは立てられなかった時で、その理由を名指す。
(val PG-SKIP-REASON (pg-skip-reason))

;; 期限を過ぎる終端の行の数(SWEEP-ROWS-BATCH を 2 回越える — 書きの錠の transaction 3 つに分かれる)。
(val MANY-ROWS (+ (* 2 SWEEP-ROWS-BATCH) 7))
;; 期限を過ぎる行の書きの冪等キーの覚えの数(FORGET-WRITE-KEYS-BATCH を 2 回越える)。
(val MANY-KEYS (+ (* 2 FORGET-WRITE-KEYS-BATCH) 7))


(defk ceiling-of [count batch]
  {:pre [(: count int) (: batch int) (> batch 0)] :post [(: % int)]
   :tags {:context "records" :role "program"}}
  "count 個を batch 個ずつに分けた組の数(transaction と往復の数の見込み)を出すため。"
  (- (// (- count) batch)))


(defk sweep-many-rows [counts]
  {:pre [(: counts StatementCounts)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "表 tickets に終端の行を MANY-ROWS 行書き、保持の秒の後に SweepExpired を 1 回撃って、#(答え 回収の間の往復の tuple)を返すため。"
  (for [n (range MANY-ROWS)]
    (<- written (PutRow "tickets" #("g" (.format "old-{}" n)) (FrozenMap {"state" "done"}) (ExpectAbsent)))
    (assert (isinstance written Written) (repr written)))
  (<- (Delay (+ TICKET-KEEP-SECONDS 1)))
  (<- outcome (effect-round-trips counts (SweepExpired)))
  outcome)


(deftest test-a-row-sweep-holds-the-numbering-lock-only-for-one-round-trip-per-bounded-batch
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections))
  (val store (prepared-store connections (fresh-prefix)))
  (val counts (StatementCounts))
  (try
    (val outcome (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (postgres-sql-handler connections)
                                                 (probe-sql-handler connections DATABASE (QueryProbe counts))
                                                 (pg-records-handler store MAKER ORIGIN-HOST)]
                                                (sweep-many-rows counts)))))
    (val swept (get outcome 0))
    (val trips (get outcome 1))
    (assert (= swept (Swept MANY-ROWS 0 0)) (repr swept))
    (<- numbering (numbering-lock-statement store.prefix))
    (val change-head (.format "INSERT INTO {}row_changes" store.prefix))
    (val changing (tuple (gfor trip trips :if (any (gfor text trip (.startswith (.strip text) change-head))) trip)))
    ;; 積む文を流す往復は ⌈MANY-ROWS / SWEEP-ROWS-BATCH⌉ 個で、どれも番号の錠・積む文・COMMIT を 1 往復に持つ。
    (<- expected (ceiling-of MANY-ROWS SWEEP-ROWS-BATCH))
    (assert (= (len changing) expected) changing)
    (for [trip changing]
      (val texts (tuple (gfor text trip (.strip text))))
      (val inserts (len (lfor text texts :if (.startswith text change-head) text)))
      (assert (in numbering.text texts) trip)
      (assert (= (get texts -1) "COMMIT") trip)
      (assert (<= 1 inserts SWEEP-ROWS-BATCH) trip)
      ;; 番号の錠の後ろは積む文と COMMIT(合図)だけ — 行の表の文は錠の前。
      (val after (cut texts (+ (.index texts numbering.text) 1) None))
      (assert (all (gfor text after (or (.startswith text change-head) (= text "COMMIT") (.startswith text "SELECT pg_notify")))) trip))
    (assert (= (sum (gfor trip changing text trip :if (.startswith (.strip text) change-head) 1)) MANY-ROWS) changing)
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))


(defk forget-many-keys [counts prefix]
  {:pre [(: counts StatementCounts) (: prefix str)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "行の書きの冪等キーの覚えを刻 0 に MANY-KEYS 個置き、覚えの長さの後に SweepExpired を 1 回撃って、#(答え 回収の間の往復の tuple
   残った覚えの数)を返すため。"
  (<- seeded (SqlQuery DATABASE (.format "INSERT INTO {p}write_keys (idempotency_key, body_digest, answer, last_seq, remembered_at)
                                          SELECT 'key-' || n, 'digest', '{{}}', 0, 0 FROM generate_series(1, {count}) AS n"
                                         :p prefix :count MANY-KEYS)
                       #()))
  (assert (isinstance seeded SqlRows) (repr seeded))
  (<- (Delay (+ WRITE-KEY-MEMORY-SECONDS 1)))
  (<- outcome (effect-round-trips counts (SweepExpired)))
  (<- left (SqlQuery DATABASE (.format "SELECT count(*) FROM {p}write_keys" :p prefix) #()))
  #((get outcome 0) (get outcome 1) (int (get left.rows 0 0))))


(deftest test-forgetting-write-keys-splits-the-numbering-lock-into-bounded-transactions
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections))
  (val store (prepared-store connections (fresh-prefix)))
  (val counts (StatementCounts))
  (try
    (val outcome (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (postgres-sql-handler connections)
                                                 (probe-sql-handler connections DATABASE (QueryProbe counts))
                                                 (pg-records-handler store MAKER ORIGIN-HOST)]
                                                (forget-many-keys counts store.prefix)))))
    (val swept (get outcome 0))
    (val trips (get outcome 1))
    (val left (get outcome 2))
    (assert (= swept (Swept 0 0 MANY-KEYS)) (repr swept))
    (assert (= left 0) left)
    ;; 外しの文は transaction ごとに 1 つ(文 1 つずつの transaction — 往復ごとに 1 文)で、⌈MANY-KEYS / FORGET-WRITE-KEYS-BATCH⌉ 個。
    (val forgets (tuple (gfor trip trips text trip
                              :if (and (.startswith (.strip text) "WITH gone AS") (in (.format "DELETE FROM {}write_keys" store.prefix) text))
                              text)))
    (<- expected (ceiling-of MANY-KEYS FORGET-WRITE-KEYS-BATCH))
    (assert (= (len forgets) expected) forgets)
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))
