;; PostgreSQL の置き場の鍵の覚えの外し(card ki-4ebaefac6668 — maintenance.hy の頭の註の「鍵の覚えを外す」)の、移行と前からの覚え。
;; 性質: 覚えた刻の列(retired_keys.remembered_at)の無い置き場(前の版が作った表)を用意すると、列が足され、前からの覚えは足した刻に
;; 覚えた物として刻を持つ。列の在る置き場の用意は列を足す文を流さない(表の排他の錠を起動のたびに取らない)。前からの覚えは、覚え方を
;; 限った列なら足した刻から長さの後の回収で外れ、外さない列(既定)の覚えは残る。
;; 反例の形: 列を足す文を毎回流す用意(2 度目の用意に ALTER が在る)・覚えた刻を持たない前からの覚え(列の既定が無い)は、ここの判定が赤。
;; env DOEFF_RECORDS_TEST_PG_DSN が無ければ conftest が使い捨ての PostgreSQL を立てて置く(#2830)。立てられなければ理由を名指して skip。
(require doeff-hy.macros [deftest val])
(import time)
(import datetime [datetime timezone timedelta])
(import doeff [run with_handlers])
(import doeff_core_effects [scheduled])
(import doeff_core_effects.postgres_sql [postgres-sql-handler])
(import doeff_core_effects.sql_effects [SqlQuery])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records [EventAbsent EventRetired])
(import doeff_records [ReadEventByKey])
(import doeff_records.maintenance [SweepExpired Swept])
(import doeff_records._laws [LAW-SCHEMA MAKER BEAT-MEMORY-SECONDS])
(import doeff_records [pg-records-handler]
        doeff_records._pg [drop-records-tables prepare-records-store])
(import tests.interpreters [session-dsn PG-DSN-VARIABLE pg-skip-reason DATABASE ORIGIN-HOST postgres-connections fresh-prefix run-sql])
(import tests.sql_probes [QueryProbe StatementCounts probe-sql-handler])

(val PG-DSN (session-dsn PG-DSN-VARIABLE))
;; env が無ければ conftest が使い捨ての PostgreSQL を立てて置く(#2830)— 無いのは立てられなかった時で、その理由を名指す。
(val PG-SKIP-REASON (pg-skip-reason))


(defn sql-rows [connections #^ str text]  ; defk にできない: 検の組み立てで別の run を回す入口
  "文 text を 1 つ流して行を返す(前の版の表の形を作る・覚えの行を直に読む検の口)。"
  (. (run-sql connections (SqlQuery DATABASE text #())) rows))


(defn counted-prepare [connections #^ str prefix]  ; defk にできない: 検の組み立てで別の run を回す入口
  "置き場を用意し、#(置き場 用意が流した文の綴りの並び) を返す。"
  (setv counts (StatementCounts)
        store (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (probe-sql-handler connections DATABASE (QueryProbe counts))]
                                             (prepare-records-store DATABASE DATABASE LAW-SCHEMA prefix)))))
  #(store (tuple (gfor flush counts.flushes text flush text))))


(defn on-store-at [connections store #^ datetime at effect]  ; defk にできない: 検の組み立てで別の run を回す入口
  "置き場 store の書き手 maker の handler の下で、時計を at に置いて effect を 1 つ撃ち、答えを返す。"
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock :current-time at)) (postgres-sql-handler connections)
                                  (pg-records-handler store MAKER ORIGIN-HOST)]
                                 effect))))


(deftest test-a-store-without-the-remembered-column-gains-it-once-and-keeps-old-keys
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val prefix (fresh-prefix))
  (val connections (postgres-connections 1))
  (val store (get (counted-prepare connections prefix) 0))
  (try
    ;; 前の版の表の形: 覚えた刻の列も覚えの外しの索引も無く、前からの覚えが在る(長さを限った列 beats・残さない列 ticks・外さない列 pulses)。
    (sql-rows connections (.format "DROP INDEX {p}retired_keys_remembered" :p prefix))
    (sql-rows connections (.format "ALTER TABLE {p}retired_keys DROP COLUMN remembered_at" :p prefix))
    (sql-rows connections (.format "INSERT INTO {p}retired_keys (ledger, idempotency_key, seq, body_digest)
                                     VALUES ('beats', 'old-beat', 1, 'd1'), ('ticks', 'old-tick', 2, 'd2'), ('pulses', 'old-pulse', 3, 'd3')
                                     RETURNING 1" :p prefix))
    (val before-ms (int (* 1000 (time.time))))
    (val first-texts (get (counted-prepare connections prefix) 1))
    (val after-ms (int (* 1000 (time.time))))
    (val altered (lfor text first-texts :if (.startswith (.lstrip text) "ALTER TABLE") text))
    (assert (= (len altered) 1) (repr altered))
    ;; 前からの覚えは足した刻(DB の時計 — 用意の前後の刻の間)を持つ。
    (val remembered (sql-rows connections (.format "SELECT idempotency_key, remembered_at FROM {p}retired_keys ORDER BY idempotency_key"
                                                   :p prefix)))
    (assert (= (lfor row remembered (get row 0)) ["old-beat" "old-pulse" "old-tick"]) (repr remembered))
    (for [row remembered]
      (assert (<= (- before-ms 1000) (get row 1) (+ after-ms 1000)) (repr #(row before-ms after-ms))))
    ;; 列の在る置き場の用意は列を足す文を流さない。
    (val third-texts (get (counted-prepare connections prefix) 1))
    (assert (not (lfor text third-texts :if (.startswith (.lstrip text) "ALTER TABLE") text)) (repr third-texts))
    ;; 前からの覚えの外し: 足した刻の直後は、残さない列の覚えだけが外れる(長さ 0)。長さの後は長さを限った列の覚えも外れる。外さない列は残る。
    (val added (datetime.fromtimestamp (/ (max (gfor row remembered (get row 1))) 1000) :tz timezone.utc))
    (val soon (on-store-at connections store (+ added (timedelta :seconds 1)) (SweepExpired)))
    (assert (= soon (Swept 0 1)) (repr soon))
    (val held (on-store-at connections store (+ added (timedelta :seconds 1)) (ReadEventByKey "beats" "old-beat")))
    (assert (= held (EventRetired 1)) (repr held))
    (val late (on-store-at connections store (+ added (timedelta :seconds (+ BEAT-MEMORY-SECONDS 1))) (SweepExpired)))
    (assert (= late (Swept 0 1)) (repr late))
    (val gone (on-store-at connections store (+ added (timedelta :seconds (+ BEAT-MEMORY-SECONDS 1))) (ReadEventByKey "beats" "old-beat")))
    (assert (= gone (EventAbsent)) (repr gone))
    (val left (sql-rows connections (.format "SELECT ledger, idempotency_key FROM {p}retired_keys" :p prefix)))
    (assert (= (lfor row left (tuple row)) [#("pulses" "old-pulse")]) (repr left))
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))
